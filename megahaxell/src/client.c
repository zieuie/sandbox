#include "megahaxell/client.h"

int mhx_client_request(const char *host, uint16_t port, const void *req, unsigned long req_len) {
  (void)host;
  (void)port;
  (void)req;
  (void)req_len;
  /* TODO: connect, send request, read response. */
  return -1;
}
