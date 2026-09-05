FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /app ./
USER node
EXPOSE 3000
CMD ["npm", "run", "start", "--", "--host", "0.0.0.0", "--port", "3000"]
