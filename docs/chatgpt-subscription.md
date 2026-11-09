# ChatGPT subscription (Sign in with ChatGPT)

[← Back to README](../README.md)

The **ChatGPT (subscription)** provider lets the owner of this self-hosted install use their own ChatGPT plan for chat, Quick Ask, and chat titles, with no API key. It follows OpenAI's [Sign in with ChatGPT open-source token path](https://developers.openai.com/siwc/token-sharing-open-source).

- **Requirements:** a ChatGPT **Plus or Pro** plan whose workspace allows plan usage in other apps.
- **Personal use only.** OpenAI limits this path to open-source and personal local projects. Adam has one owner and no multi-user support. Do not expose Adam to other people while you are signed in.

## Setup

1. Start Adam. With Docker, `compose.yaml` publishes the sign-in callback on the host as `127.0.0.1:${ADAM_CHATGPT_CALLBACK_PORT:-1455}`. For local development, the API process listens on `127.0.0.1:1455` itself. Port 1455 must be free; if another app is using it, set `ADAM_CHATGPT_CALLBACK_PORT` to another port.
2. Open Settings (`Cmd/Ctrl + ,`) → **ChatGPT (subscription)** → **Sign in with ChatGPT**. A ChatGPT tab opens. Approve access, including plan usage.
3. ChatGPT redirects to `http://127.0.0.1:1455/auth/callback`. Adam finishes signing in and the tab says you can close it. Settings then shows your account and loads the models your plan can use (`GET /v1/models`, entries with `visibility == "list"`).
4. Pick a model and click **Apply model**. To use ChatGPT for chat titles too, choose it in **Chat titles**.

Credentials are stored in `./data/chatgpt_auth.json` with `0600` permissions and written atomically. They include the issued `oaiapp_…` client ID, a random host ID, account identity, the access, refresh, and ID tokens, and expiry times. Tokens are never logged or sent to the browser; the UI only sees status (signed-in email and expiry). Access tokens last about an hour and refresh automatically when needed. Each refresh replaces the 30-day refresh token, and refreshes are serialized. A background job refreshes at least once a day while Adam runs, so the session stays alive as long as Adam is running at least every few weeks. **Sign out** revokes the refresh token with OpenAI and deletes the local tokens.

## Using Adam from another device (Tailscale, LAN)

The callback address is `127.0.0.1`, which always means "this device". If your browser runs on another device, such as a MacBook reaching your PC over Tailscale, the final redirect fails on that device with a "can't connect" page. That failure is expected and the sign-in can still be finished:

1. In Settings → **ChatGPT (subscription)**, click **Sign in with ChatGPT** and approve access in the new tab.
2. When the tab shows the connection error, copy the **entire address** from its address bar. It starts with `http://127.0.0.1:1455/auth/callback?code=…&state=…`.
3. Back in Adam, open **Using Adam from another device?**, paste the URL into **Callback URL**, and click **Finish sign-in**.

The server checks the pasted `state` against the pending sign-in (one use, 15-minute lifetime) and completes the PKCE exchange itself. Paste the URL promptly; if it has expired, start the sign-in again.

## Preview limitations and errors

- Plan usage is a preview. Inference uses only `POST https://api.openai.com/v1/responses` with `store: false` and `stream: true`. Adam never sends parameters that plan usage rejects, including `temperature`, `top_p`, `max_output_tokens`, `previous_response_id`, and `user`, so answers use the model's default sampling.
- Audio and video input, hosted tools, and file uploads are not available on this path. Adam only sends text and selected-region images.
- **ChatGPT plan limit reached** means `subscription_sharing_usage_limit_exceeded` (HTTP 429). Adam pauses ChatGPT requests for a short while and links to [ChatGPT usage settings](https://chatgpt.com/settings/usage). OpenAI does not report a reset time, so Adam does not show one; Plus plans share a rolling limit across all apps, and an app-specific limit may also apply. Use **Resume now** in Settings to try again sooner.
- **Sign in again** means the session was revoked, expired, or disconnected from ChatGPT settings. Click **Sign in again with ChatGPT**. Adam reuses its saved app registration.
- If the account or workspace is not eligible for plan usage, or the plan scope was declined, Settings says so. Use another provider in that case.

