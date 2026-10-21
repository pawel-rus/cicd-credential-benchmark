# Custom GitLab Runner Image with Python 3 for Shell Executor Benchmarking
FROM gitlab/gitlab-runner:latest

# Install Python 3 for executing native benchmark scripts in Shell mode
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 && \
    rm -rf /var/lib/apt/lists/*
