# stickerdaniel/linkedin-mcp-server, pinned, plus one patch: post search clicks "Load more".
# LinkedIn's post search no longer loads results by infinite scroll; it shows a "Load more"
# button, so the server's scrolling returned only the first ~3 posts (seen 2026-10-23).
# patch_load_more.py fails the build if the code it patches has moved.
FROM stickerdaniel/linkedin-mcp-server@sha256:6b94a5a4d40aba1475725d34f0d34ebc812026ddab0fd3d5c8494fa48b5c4160
USER root
COPY patch_load_more.py /tmp/patch_load_more.py
RUN /app/.venv/bin/python /tmp/patch_load_more.py && rm /tmp/patch_load_more.py
USER pwuser
