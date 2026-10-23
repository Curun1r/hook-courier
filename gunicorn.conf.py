import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
chdir = "/app/src"
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "sync"
timeout = 30
graceful_timeout = 30
worker_tmp_dir = "/dev/shm"
control_socket_disable = True
accesslog = "-"
errorlog = "-"
loglevel = "info"
# U is the URL path without its query string; never log headers or request bodies.
access_log_format = "%(h)s %(m)s %(U)s %(s)s %(L)s"
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1")
