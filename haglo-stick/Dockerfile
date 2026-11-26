ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3
COPY stick.py /stick.py
# Ohne s6-Init: sonst kommt SUPERVISOR_TOKEN nicht im Skript an
ENTRYPOINT []
CMD ["python3", "-u", "/stick.py"]
