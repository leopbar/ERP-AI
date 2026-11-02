FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV API_PROXY_URL=http://backend:8001
RUN npm run build
CMD ["npm", "start"]
