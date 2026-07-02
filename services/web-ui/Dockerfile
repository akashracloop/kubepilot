# Web UI image (Next.js). Build with this directory as context:
#   docker build -t kubepilot-web-ui:dev services/web-ui
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
EXPOSE 3000
USER node
CMD ["npm", "start"]
