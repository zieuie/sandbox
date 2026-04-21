#ifndef MEGAHAXELL_CLIENT_H
#define MEGAHAXELL_CLIENT_H

#include <stdint.h>

/* Network client placeholder. Returns 0 on success, <0 on error. */
int mhx_client_request(const char *host, uint16_t port, const void *req, unsigned long req_len);

#endif /* MEGAHAXELL_CLIENT_H */
