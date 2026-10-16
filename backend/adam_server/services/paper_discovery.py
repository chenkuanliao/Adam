"""Paper discovery and bounded, DNS-pinned public PDF downloads."""
import asyncio
import ipaddress
import re
import socket
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document
from ..schemas import PaperCandidate

ARXIV_ID = re.compile(r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/)?((?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)(?:\.pdf)?$", re.I)
ATOM = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
_cache: dict[str, tuple[float, list[PaperCandidate]]] = {}
_arxiv_lock = asyncio.Lock()
_arxiv_last = 0.0


def public_url(value: str) -> str:
    """Syntactic gate; DNS and redirect destinations are checked at connection time."""
    try:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo or url.port not in {None, 80, 443}:
            raise ValueError()
        return str(url)
    except (ValueError, httpx.InvalidURL):
        raise HTTPException(400, "Use a public HTTP or HTTPS paper link.") from None


async def public_address(host: str, port: int) -> str:
    try:
        addresses = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM), 8)
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise HTTPException(400, "Paper links must point to a public internet address.")
        return ips[0]
    except (OSError, ValueError, asyncio.TimeoutError):
        raise HTTPException(400, "The paper host could not be resolved.") from None


@asynccontextmanager
async def public_stream(url: str):
    """Validate each redirect and pin the validated IP, retaining TLS SNI/Host.

    Connecting to the IP avoids a second DNS lookup between validation and use.
    No cookies, environment proxies, or credentials are forwarded.
    """
    current = public_url(url)
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), trust_env=False) as client:
        for _ in range(6):
            original = httpx.URL(current)
            address = await public_address(original.host, original.port or (443 if original.scheme == "https" else 80))
            pinned = original.copy_with(host=address)
            async with client.stream("GET", pinned, headers={"Host": original.netloc.decode(), "User-Agent": "Adam-Paper-Reader/0.1", "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.5"}, extensions={"sni_hostname": original.host}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise HTTPException(400, "The paper link returned an invalid redirect.")
                    destination = public_url(urljoin(current, location))
                    if original.scheme == "https" and httpx.URL(destination).scheme != "https":
                        raise HTTPException(400, "The paper link redirected to an insecure connection.")
                    current = destination
                    continue
                if response.status_code != 200:
                    raise HTTPException(400, f"The source returned HTTP {response.status_code}. Try another PDF link or upload the file.")
                yield response, current
                return
        raise HTTPException(400, "The paper link redirected too many times.")


def arxiv_id(value: str) -> str | None:
    value = value.strip().split("?")[0].rstrip("/")
    if "://" in value:
        parsed = urlparse(value)
        if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            return None
        value = "arxiv.org" + parsed.path
    match = ARXIV_ID.fullmatch(value)
    return match.group(1) if match else None


async def search_arxiv(query: str) -> list[PaperCandidate]:
    global _arxiv_last
    key = query.casefold()
    async with _arxiv_lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return [paper.model_copy(deep=True) for paper in cached[1]]
        # arXiv requests are serial and at least three seconds apart.
        await asyncio.sleep(max(0, 3 - (time.monotonic() - _arxiv_last)))
        identifier = arxiv_id(query)
        words = re.findall(r'[\w-]+', query)[:30]
        params = {"id_list": identifier} if identifier else {"search_query": " AND ".join(f'all:"{word}"' for word in words), "sortBy": "relevance"}
        if not identifier:
            params.update({"start": "0", "max_results": "12"})
        try:
            async with httpx.AsyncClient(timeout=18, trust_env=False, follow_redirects=True) as client:
                response = await client.get("https://export.arxiv.org/api/query", params=params, headers={"User-Agent": "Adam-Paper-Reader/0.1"})
                response.raise_for_status()
            root = ET.fromstring(response.content)
        finally:
            _arxiv_last = time.monotonic()
        results = []
        for entry in root.findall("a:entry", ATOM):
            identifier = arxiv_id(entry.findtext("a:id", "", ATOM))
            title = " ".join(entry.findtext("a:title", "", ATOM).split())
            if not identifier or not title:
                continue
            results.append(PaperCandidate(title=title[:1000], authors=[author.findtext("a:name", "", ATOM) for author in entry.findall("a:author", ATOM)][:100], year=entry.findtext("a:published", "", ATOM)[:4], abstract=" ".join(entry.findtext("a:summary", "", ATOM).split())[:15000], source="arXiv", source_url=f"https://arxiv.org/abs/{identifier}", pdf_urls=[f"https://arxiv.org/pdf/{identifier}"], arxiv_id=identifier, doi=entry.findtext("ar:doi", None, ATOM)))
        if len(_cache) >= 64:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), results)
        return [paper.model_copy(deep=True) for paper in results]


async def search_general(base_url: str, query: str) -> list[PaperCandidate]:
    async with httpx.AsyncClient(timeout=18, trust_env=False) as client:
        response = await client.get(f"{base_url.rstrip('/')}/search", params={"q": f"{query} paper pdf", "format": "json", "categories": "general"})
        response.raise_for_status()
    payload = response.json()
    if payload.get("unresponsive_engines") and not payload.get("results"):
        raise ValueError("Search engines are unavailable")
    results = []
    for item in payload.get("results", [])[:12]:
        url = str(item.get("url") or "")
        try:
            public_url(url)
        except HTTPException:
            continue
        identifier = arxiv_id(url)
        pdfs = [f"https://arxiv.org/pdf/{identifier}"] if identifier else [url] if urlparse(url).path.lower().endswith(".pdf") else []
        results.append(PaperCandidate(title=str(item.get("title") or urlparse(url).hostname)[:1000], abstract=str(item.get("content") or "")[:15000], source="arXiv" if identifier else str(urlparse(url).hostname), source_url=f"https://arxiv.org/abs/{identifier}" if identifier else url, pdf_urls=pdfs, arxiv_id=identifier))
    return results


def existing_paper(db: Session, candidate: PaperCandidate) -> Document | None:
    for document in db.scalars(select(Document)):
        metadata = document.discovery
        # A versioned arXiv ID takes priority over a DOI shared by versions.
        if candidate.arxiv_id:
            if metadata.get("arxiv_id") == candidate.arxiv_id:
                return document
        elif candidate.doi and (metadata.get("doi") or "").casefold() == candidate.doi.casefold():
            return document
        elif metadata.get("source_url") == candidate.source_url and not metadata.get("arxiv_id"):
            return document
    return None


async def discover(query: str, base_url: str, db: Session) -> dict:
    outcomes = await asyncio.gather(search_arxiv(query), search_general(base_url, query), return_exceptions=True)
    results: list[PaperCandidate] = []
    warnings = []
    seen = {}
    for label, outcome in zip(("arXiv", "Web search"), outcomes):
        if isinstance(outcome, BaseException):
            warnings.append(f"{label} is temporarily unavailable. You can retry or use another source.")
            continue
        for paper in outcome:
            key = paper.arxiv_id or (paper.doi or "").casefold() or paper.source_url
            if key in seen:
                previous = seen[key]
                previous.pdf_urls = list(dict.fromkeys(previous.pdf_urls + paper.pdf_urls))[:10]
                continue
            seen[key] = paper
            existing = existing_paper(db, paper)
            paper.existing_document_id = existing.id if existing else None
            results.append(paper)
    return {"results": results, "warnings": warnings}


class PdfLinks(HTMLParser):
    def __init__(self, base: str):
        super().__init__()
        self.base = base
        self.urls: list[str] = []
        self.title = ""
        self.authors: list[str] = []
        self.year = ""
        self.doi = ""

    def handle_starttag(self, tag: str, attrs):
        attributes = dict(attrs)
        name = str(attributes.get("name", "")).lower()
        content = str(attributes.get("content") or "").strip()
        if tag == "meta":
            if name == "citation_title": self.title = content[:1000]
            elif name == "citation_author" and content and len(self.authors) < 100: self.authors.append(content[:500])
            elif name in {"citation_publication_date", "citation_date"}: self.year = content[:4]
            elif name == "citation_doi": self.doi = content[:300]
        value = None
        if tag == "meta" and name in {"citation_pdf_url", "eprints.document_url", "wkhealth_pdf_url"}:
            value = attributes.get("content")
        elif tag in {"a", "link"}:
            href = attributes.get("href") or ""
            if attributes.get("type") == "application/pdf" or urlparse(href).path.lower().endswith(".pdf"):
                value = href
        if value:
            try:
                url = public_url(urljoin(self.base, value))
                if url not in self.urls and len(self.urls) < 10:
                    self.urls.append(url)
            except HTTPException:
                pass


async def resolve_pdf(paper: PaperCandidate) -> PaperCandidate:
    async with public_stream(paper.source_url) as (response, final_url):
        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type:
            paper.pdf_urls = list(dict.fromkeys([final_url] + paper.pdf_urls))[:10]
        elif "html" in content_type:
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                data.extend(chunk)
                if len(data) >= 1_000_000:
                    break
            parser = PdfLinks(final_url)
            parser.feed(data.decode("utf-8", errors="replace"))
            paper.pdf_urls = list(dict.fromkeys(paper.pdf_urls + parser.urls))[:10]
            paper.title = parser.title or paper.title
            paper.authors = parser.authors or paper.authors
            paper.year = parser.year or paper.year
            paper.doi = parser.doi or paper.doi
    return paper


async def download_pdf(url: str, title: str, max_bytes: int) -> UploadFile:
    spool = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024, mode="w+b")
    try:
        # An overall limit supplements httpx's per-read timeout.
        async with asyncio.timeout(120):
            async with public_stream(url) as (response, _):
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise HTTPException(413, "This PDF exceeds the configured file size limit.")
                size = 0
                signature = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise HTTPException(413, "This PDF exceeds the configured file size limit.")
                    if len(signature) < 5:
                        signature.extend(chunk[:5 - len(signature)])
                        if len(signature) == 5 and signature != b"%PDF-":
                            raise HTTPException(400, "This link returned a web page or another file, rather than a PDF. Try another source or upload the PDF.")
                    spool.write(chunk)
                if signature != b"%PDF-":
                    raise HTTPException(400, "The source did not return a PDF.")
        spool.seek(0)
        filename = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", title).strip(" .")[:220] or "paper"
        return UploadFile(file=spool, filename=filename.removesuffix(".pdf") + ".pdf")
    except BaseException:
        spool.close()
        raise
