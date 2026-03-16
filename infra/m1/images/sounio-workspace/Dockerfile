ARG BASE_IMAGE=localhost/sounio-dev:toolchain-v1
FROM ${BASE_IMAGE}

RUN useradd --create-home --shell /bin/bash coder || true
RUN mkdir -p /workspace /workspace/src /workspace/home && chown -R coder:coder /workspace

USER coder
ENV HOME=/workspace/home
WORKDIR /workspace

CMD ["sleep", "infinity"]
