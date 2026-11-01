FROM node:24-alpine

WORKDIR /app

# Copied first so code changes do not reinstall dependencies.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Next.js writes this value into the browser code during the build.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
