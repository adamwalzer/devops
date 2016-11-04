FROM cmwn/front-node:6-wheezy
MAINTAINER Chuck "MANCHUCK" Reeves <chuck@manchuck.com>

RUN apt-get update && apt-get install -y --no-install-recommends \
    && wget http://ftp.ruby-lang.org/pub/ruby/2.3/ruby-2.3.1.tar.gz \
    && tar -xzvf ruby-2.3.1.tar.gz \
    && cd ruby-2.3.1/ \
    && ./configure \
    && make \
    && make install \
    && ruby -v \
    && gem install scss_lint \
    && npm install -gq gulp eslint eslint-plugin-react babel-eslint scss-lint gulp-scss-lint gulp-scss-lint-stylish webdriverio wdio-mocha-framework babel-core

WORKDIR /var/www
