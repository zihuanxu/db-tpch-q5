FROM continuumio/miniconda3:24.11.1-0

WORKDIR /workspace
COPY environment-arrow-cpu.yml /tmp/environment.yml
RUN conda env create --file /tmp/environment.yml && conda clean --all --yes

COPY . /workspace
RUN conda run --no-capture-output -n memq5-arrow-cpu bash scripts/ci_cpu.sh

CMD ["conda", "run", "--no-capture-output", "-n", "memq5-arrow-cpu", "ctest", "--test-dir", "build-ci-arrow", "--output-on-failure"]
