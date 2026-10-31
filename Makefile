.PHONY: install test demo demo-fast demo-video eval clean

install:
	pip install -e ".[dev]"

test:
	pytest -v tests/

demo:
	python demo.py --pace 1.0

demo-fast:
	python demo.py --fast

demo-video:
	python demo.py --video

eval:
	python eval/run_eval.py --trials 20

clean:
	python tasks.py clean
