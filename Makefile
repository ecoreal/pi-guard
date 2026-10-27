PYTHON ?= python3
SRC := src
export PYTHONPATH := $(CURDIR)/$(SRC)

.PHONY: test demo zipapp clean

test:
	$(PYTHON) -m unittest discover -s tests -t tests -v

# Watch a simulated Pi heat up, throttle, and recover — on any machine.
demo:
	$(PYTHON) -m pi_guard.cli run --simulate --scenario throttle \
		--interval 0.3 --max-ticks 25 --warn-temp 70 --crit-temp 80

zipapp:
	@mkdir -p dist
	$(PYTHON) -m zipapp $(SRC) -o dist/pi-guard \
		-m "pi_guard.cli:main" -p "/usr/bin/env python3" --compress
	chmod +x dist/pi-guard
	@echo "Built dist/pi-guard — scp it to your Pi and run."

clean:
	rm -rf dist build src/*.egg-info
