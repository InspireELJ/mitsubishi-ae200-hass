ARG BUILD_FROM
FROM ${BUILD_FROM}

# Python + the bridge dependencies. bashio (from the HA base image) is used by
# run.sh to read add-on options and the auto-discovered MQTT service.
RUN apk add --no-cache python3 py3-pip

# Install the bridge dependencies. pymelans is vendored from this repo so the
# add-on and the HACS custom component share one protocol implementation.
COPY requirements.txt /tmp/requirements.txt
COPY pymelans /tmp/pymelans
RUN pip3 install --no-cache-dir --break-system-packages \
        -r /tmp/requirements.txt \
        /tmp/pymelans \
    && rm -rf /tmp/pymelans /tmp/requirements.txt

# Copy the add-on filesystem (s6 service + bridge program)
COPY rootfs /

RUN chmod a+x /usr/bin/ae200_bridge.py \
    && chmod a+x /etc/services.d/ae200bridge/run \
    && chmod a+x /etc/services.d/ae200bridge/finish

LABEL \
    io.hass.name="Mitsubishi AE-200" \
    io.hass.description="AE-200/AE-50/EW-50 to Home Assistant via MQTT Discovery" \
    io.hass.type="addon" \
    io.hass.version="0.1.0"
