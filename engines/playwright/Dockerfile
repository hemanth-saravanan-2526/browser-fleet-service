FROM public.ecr.aws/lambda/python:3.12

# Install system dependencies required for headless Chromium
RUN dnf install -y \
    alsa-lib \
    atk \
    cups-libs \
    gtk3 \
    ipa-gothic-fonts \
    libXcomposite \
    libXcursor \
    libXdamage \
    libXext \
    libXi \
    libXrandr \
    libXScrnSaver \
    libXtst \
    pango \
    xorg-x11-fonts-100dpi \
    xorg-x11-fonts-75dpi \
    xorg-x11-fonts-misc \
    xorg-x11-fonts-Type1 \
    xorg-x11-utils \
    nss \
    nspr \
    && dnf clean all

# Copy requirements and install
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Install Playwright Chromium binary
RUN PLAYWRIGHT_BROWSERS_PATH=/opt/playwright python -m playwright install chromium

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# Copy handler and extractor code
COPY handler.py extractor.py ${LAMBDA_TASK_ROOT}/

CMD [ "handler.handler" ]
