# =============================================================================
# crs-copilot-cli Patcher Module
# =============================================================================
# RUN phase: Receives POVs, generates patches using Copilot CLI,
# tests them using the snapshot image for incremental rebuilds.
#
# Uses host Docker socket (mounted by framework) to access snapshot images.
# =============================================================================

# These ARGs are required by the oss-crs framework template
ARG target_base_image
ARG crs_version

FROM copilot-cli-base

# Install libCRS (CLI + Python package)
COPY --from=libcrs . /libCRS
RUN pip3 install /libCRS \
    && python3 -c "from libCRS.base import DataType; print('libCRS OK')"

# Install crs-copilot-cli package (patcher + agents)
COPY pyproject.toml /opt/crs-copilot-cli/pyproject.toml
COPY patcher.py /opt/crs-copilot-cli/patcher.py
COPY agents/ /opt/crs-copilot-cli/agents/
RUN pip3 install /opt/crs-copilot-cli

CMD ["run_patcher"]
