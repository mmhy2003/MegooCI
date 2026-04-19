FROM node:22-alpine AS base
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM base AS dev
COPY . .
CMD ["npm", "run", "dev"]

FROM base AS builder
COPY . .
RUN npm run build

FROM node:22-alpine AS production
WORKDIR /app
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
ENV PORT=3000
CMD ["node", "server.js"]
