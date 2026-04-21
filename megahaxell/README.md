
# megahaxell

A C project built with `make`. The current code is a skeleton: it compiles, runs, and has a small smoke test, but most modules are TODO.

This version uses ZeroMQ (libzmq) for process-to-process networking.

## Quickstart (Make)

```sh
make -C megahaxell
# Terminal 1:
./megahaxell/build/megahaxell --n 12 --d 3 --eps 0.1 --port 9001 --workers 2
# Terminal 2:
./megahaxell/build/megahaxell --worker 127.0.0.1:9001 --workers 2
# Or (port defaults to 9001 if omitted):
./megahaxell/build/megahaxell --worker 192.168.1.10 --workers 2
make -C megahaxell test
```

Workers can be started before the head; they will keep retrying and will reconnect if the head restarts. Add `--verbose` on the worker to see reconnect logs.

### Resilience Model

The network model is now one supervisor process per computer. That supervisor sends one heartbeat stream to the head and manages multiple local math slots.

- `megahaxell --worker HOST --workers N` starts one Linux supervisor with `N` local math slots.
- The supervisor heartbeats to the head every few seconds and includes the list of still-running local jobs.
- If the head restarts, the next supervisor heartbeat rebuilds that host's in-flight job table.
- If the supervisor disappears or stops heartbeating for long enough, those leases expire and the colors go back into circulation.
- The supervisor tries to run at a higher scheduler priority than the math children, and the math children attempt a smaller priority boost of their own on Linux.

This means a lost host or transient network partition should at worst cause duplicated work, not permanently lost colors or a stuck core count.

### Checkpoint/Resume

The head process periodically checkpoints the partial transversal to a file (default: `partial_pa_N_D.txt`) and resumes from it on startup.

```sh
./megahaxell/build/megahaxell --n 12 --d 3 --port 9001 --workers 4 --save-interval 30
# stop with Ctrl-C, then restart:
./megahaxell/build/megahaxell --n 12 --d 3 --port 9001 --workers 4
```

## Modules (planned)

* `core.c`: heavy compute on one process
* `pool.c`: manage worker processes / task delegation
* `serial.c`: serialize and deserialize data
* `head.c`: head process that accepts worker connections and coordinates work
* `worker.c`: worker process that connects to a head and runs a local pool
