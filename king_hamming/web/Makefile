.PHONY: check clean

check:
	python3 -m py_compile *.py tests/*.py
	./server.py >/dev/null
	cd tests && python3 test_snapshot.py
	cd tests && python3 test_server.py
	cd tests && python3 test_views.py

clean:
	rm -rf __pycache__ tests/__pycache__
