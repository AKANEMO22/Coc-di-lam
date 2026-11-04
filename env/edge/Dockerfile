# Single entry point of the stack: public UI on 8080, password-protected admin tools on 8081-8085.
FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY default.conf /etc/nginx/conf.d/default.conf
COPY dpl/ /etc/nginx/dpl/
COPY 40-dpl-edge.sh /docker-entrypoint.d/40-dpl-edge.sh
# Tolerate a CRLF checkout on Windows.
RUN sed -i 's/\r$//' /docker-entrypoint.d/40-dpl-edge.sh && chmod 755 /docker-entrypoint.d/40-dpl-edge.sh
EXPOSE 8080 8081 8082 8083 8084 8085
HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=10s \
  CMD wget -q --spider http://127.0.0.1:8080/__edge/health || exit 1
