FROM nginx:1.27.5-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
ENV NGINX_ENVSUBST_OUTPUT_DIR=/etc/nginx
COPY docker/gateway.conf.template /etc/nginx/templates/nginx.conf.template
