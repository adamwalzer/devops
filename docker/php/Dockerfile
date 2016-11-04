FROM php:7.0-fpm
MAINTAINER Chuck "MANCHUCK" Reeves <chuck@manchuck.com>

COPY composer.json /root/.composer/composer.json

# Install modules
RUN apt-get update && apt-get install -y \
        libfreetype6-dev \
        libmcrypt-dev \
    && apt-get install git mysql-client -y -q \
    && git clone --depth=1 -b php7 https://github.com/phpredis/phpredis.git /usr/src/php/ext/redis \
    && docker-php-ext-install iconv mcrypt \
    && docker-php-ext-install pdo \
    && docker-php-ext-install bcmath \
    && docker-php-ext-install pdo_mysql \
    && docker-php-ext-install zip \
    && docker-php-ext-install redis \
    && curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer \
    && cd /root/.composer/ \
    && composer global install --optimize-autoloader -vvv

RUN yes | pecl install xdebug

RUN pecl install -o -f xdebug \
    && echo "zend_extension=$(find /usr/local/lib/php/extensions/ -name xdebug.so)" > /usr/local/etc/php/conf.d/xdebug.ini \
    && echo "xdebug.remote_enable=on" >> /usr/local/etc/php/conf.d/xdebug.ini \
    && echo "xdebug.remote_autostart=off" >> /usr/local/etc/php/conf.d/xdebug.ini \
    && echo "xdebug.remote_connect_back=Off" >> /usr/local/etc/php/conf.d/xdebug.ini \
    && echo "xdebug.remote_port=9001" >> /usr/local/etc/php/conf.d/xdebug.ini

WORKDIR /var/www

CMD ["/usr/local/sbin/php-fpm"]