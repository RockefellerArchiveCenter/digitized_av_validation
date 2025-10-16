FROM python:3.12-alpine AS base

WORKDIR /code
ENV BUILD_DIR=/code/build

# Create build directory
RUN mkdir ${BUILD_DIR}

# Install system requirements and dependencies for MediaConch
RUN apk add --no-cache \
    git \
    automake \
    autoconf \
    libtool \
    make \
    g++ \
    zlib \
    libxml2 \
    libxslt \
    libxml2-dev \
    libxslt-dev \
    libevent-dev \
    jansson-dev \
    sqlite-dev \
    libzen \
    libzen-dev \
    libmediainfo \
    libmediainfo-dev

# Clone ZenLib, MediaInfoLib and MediaConch_SourceCode
RUN cd ${BUILD_DIR} && \
    git clone https://github.com/MediaArea/ZenLib.git && \
    git clone https://github.com/MediaArea/MediaInfoLib.git && \
    git clone https://github.com/MediaArea/MediaConch_SourceCode.git

# Build ZenLib
RUN cd ${BUILD_DIR}/ZenLib/Project/GNU/Library && \
    ./autogen.sh && \
    ./configure --enable-static && \
    make

# Build MediaInfoLib
RUN cd ${BUILD_DIR}/MediaInfoLib/Project/GNU/Library && \
    ./autogen.sh && \
    ./configure --enable-static && \
    cp ${BUILD_DIR}/MediaInfoLib/Project/GNU/Library/libmediainfo-config /bin/ && \
    cp ${BUILD_DIR}/ZenLib/Project/GNU/Library/libzen-config /bin/libzen-config && \
    cp ${BUILD_DIR}/ZenLib/Project/GNU/Library/libtool /bin/libtool

# Build MediaConch
RUN cd ${BUILD_DIR}/MediaConch_SourceCode/Project/GNU/CLI && \
    ./autogen.sh && \
    ./configure && \
    make

# Move mediaconch to the bin folder
RUN cp ${BUILD_DIR}/MediaConch_SourceCode/Project/GNU/CLI/.libs/mediaconch /bin/mediaconch

# Cleanup ZenLib, MediaInfoLib and MediaConch_SourceCode repositories and temporary build tools
RUN rm -rf ${BUILD_DIR} && \
    rm /bin/libzen-config && \
    rm /bin/libmediainfo-config && \
    rm /bin/libtool

# Install Python requirements
COPY requirements.txt .
RUN pip install -r requirements.txt

# Add code
COPY src src
COPY mediaconch mediaconch

FROM base AS test
COPY test_requirements.txt .coveragerc ./
RUN pip install -r test_requirements.txt
COPY tests tests

FROM base AS build
CMD [ "python", "src/validate.py" ]