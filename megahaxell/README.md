
# megahaxell

A C project built with `make`. The current code is a skeleton: it compiles, runs, and has a small smoke test, but most modules are TODO.

This version uses ZeroMQ (libzmq) for process-to-process networking.

## Quickstart (Make)

```sh
make -C megahaxell
# Terminal 1:
./megahaxell/build/megahaxell-head --n 12 --d 3 --eps 0.1 --bind tcp://*:9001 --local-workers 2
# Terminal 2:
./megahaxell/build/megahaxell-worker 127.0.0.1:9001 --workers 2
# Or (port defaults to 9001 if omitted):
./megahaxell/build/megahaxell-worker 192.168.1.10 --workers 2
make -C megahaxell test
```

## Modules (planned)

* `core.c`: heavy compute on one process
* `pool.c`: manage worker processes / task delegation
* `serial.c`: serialize and deserialize data
* `head.c`: head process that accepts worker connections and coordinates work
* `worker.c`: worker process that connects to a head and runs a local pool
