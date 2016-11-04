FROM ubuntu:14.04
MAINTAINER Chuck "MANCHUCK" Reeves <chuck@manchuck.com>

RUN apt-get update && apt-get install -y \
    nginx openssl

# The entrypoint creates a self signed ssl cert
COPY entrypoint.sh /opt/entrypoint.sh

RUN chmod a+x /opt/entrypoint.sh
RUN /opt/entrypoint.sh

RUN rm -rf /ect/nginx/sited-enabled/defualt

COPY nginx.conf /etc/nginx/nginx.conf
COPY vhosts.conf /etc/nginx/conf.d/vhosts.conf

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]