# printcheck in a container: the same environment on Linux, macOS and Windows.
#
#   docker build -t printcheck .
#   docker run --rm -v "$PWD:/work" printcheck part.stl            # judge a file in the current folder
#   docker run --rm --entrypoint python printcheck -m pytest       # the suite, 100 % coverage gate
FROM python:3.12-slim

WORKDIR /printcheck
COPY . .
RUN pip install --no-cache-dir -e ".[dev]"

# Files to judge are mounted here; relative paths on the command line resolve against it.
WORKDIR /work
ENTRYPOINT ["printcheck"]
CMD ["--help"]
