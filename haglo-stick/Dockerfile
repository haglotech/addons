ARG BUILD_FROM
FROM $BUILD_FROM
RUN apk add --no-cache python3
COPY stick.py /stick.py
CMD ["python3", "-u", "/stick.py"]
