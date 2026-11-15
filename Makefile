.PHONY: install lint test build clean

PY_PLATFORMS = --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64

install:
	pip install -e ".[dev]"

lint:
	ruff check src jobs tests
	ruff format --check src jobs tests

test:
	pytest

# Everything Terraform deploys is built here, once, and the same files go to dev and prod.
build: clean
	mkdir -p build/lambda build/layer/python build/glue
	# 1. rde package for both Lambdas (no third-party deps: boto3 is in the runtime)
	cp -r src/rde build/lambda/
	cd build/lambda && zip -qr ../lambda.zip rde -x '*/__pycache__/*'
	# 2. layer for the extract Lambda: PyAV + numpy + Pillow built for Lambda's Linux
	pip install --quiet --target build/layer/python $(PY_PLATFORMS) --implementation cp \
	  --python-version 3.12 --only-binary=:all: "av>=12" "numpy>=1.26" "pillow>=10"
	cd build/layer && zip -qr ../extract_layer.zip python
	# 3. rde package for Glue (--extra-py-files)
	cp -r src/rde build/glue/
	cd build/glue && zip -qr ../rde.zip rde -x '*/__pycache__/*'
	@ls -lh build/*.zip

clean:
	rm -rf build
