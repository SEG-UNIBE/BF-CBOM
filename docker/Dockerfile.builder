# docker/Dockerfile.builder
# Convenience image that bundles docker/compose, git, and make so the project
# can be bootstrapped from within a container while talking to the host Docker
# daemon via the mounted socket.

FROM docker:27.3.1-cli

# Add a few GNU utilities required by the Makefile workflow
RUN apk add --no-cache \
    bash \
    git \
    make \
    coreutils

WORKDIR /workspace

# Default to Bash for interactive use; compose files can override the command.
ENTRYPOINT ["/bin/bash"]
CMD ["-l"]
