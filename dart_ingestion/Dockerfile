# Stage 1: AOT compile to self-contained native binary (~10 MB, no Dart VM runtime)
FROM dart:3.3 AS build
WORKDIR /app
COPY pubspec.* ./
RUN dart pub get
COPY . .
RUN dart compile exe lib/main.dart -o /app/server

# Stage 2: minimal Debian runtime — only the binary and SSL certs
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libssl3 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/server /app/server

ENV DB_HOST=postgres \
    DB_PORT=5432 \
    DB_NAME=football_db \
    DB_USER=analytics \
    DB_PASS=analytics \
    PORT=8090

EXPOSE 8090
USER nobody
CMD ["/app/server"]
