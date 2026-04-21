#ifndef MEGAHAXELL_SERVER_H
#define MEGAHAXELL_SERVER_H

#include <stdint.h>

/* Network server placeholder. Returns 0 on success, <0 on error. */
int mhx_server_start(const char *bind_addr, uint16_t port);
void mhx_server_stop(void);

#endif /* MEGAHAXELL_SERVER_H */
