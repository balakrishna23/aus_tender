# Helper for Scrapy-Playwright to abort noisy/irrelevant requests that slow down pages
# Use in settings as: PLAYWRIGHT_ABORT_REQUEST = "playwright_abort.should_abort_request"

def should_abort_request(request):
    url = request.url.lower()
    # Block fonts/images entirely
    if request.resource_type in ("image", "font"):
        return True
    # Common asset/file extensions
    for ext in (".woff2", ".woff", ".ttf", ".svg", ".png", ".jpg", ".jpeg", ".gif"):
        if url.endswith(ext):
            return True
    # Analytics/heartbeat noise that prevents idle
    noisy_hosts = (
        "googletagmanager.com",
        "google-analytics.com",
        "api-appsee.service-now.com",
    )
    if any(h in url for h in noisy_hosts):
        return True
    return False
