#include "megahaxell/server.h"

int mhx_server_start(const char *bind_addr, uint16_t port) {
  (void)bind_addr;
  (void)port;
  /* TODO: accept connections, decode request, delegate work, respond. */
  return -1;
}

void mhx_server_stop(void) {
  /* TODO */
}
