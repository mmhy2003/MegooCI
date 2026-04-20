.PHONY: build tidy test vet lint fmt docker snapshot clean

BINARY      := megooci-agent
VERSION     ?= 0.1.0-dev
COMMIT      := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE        := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

LDFLAGS := -s -w \
	-X github.com/megooci/megooci-agent/internal/version.Version=$(VERSION) \
	-X github.com/megooci/megooci-agent/internal/version.Commit=$(COMMIT) \
	-X github.com/megooci/megooci-agent/internal/version.Date=$(DATE)

build:
	CGO_ENABLED=0 go build -trimpath -ldflags "$(LDFLAGS)" \
		-o bin/$(BINARY) ./cmd/megooci-agent

tidy:
	go mod tidy

test:
	go test ./...

vet:
	go vet ./...

fmt:
	gofmt -w .

docker:
	docker build --build-arg VERSION=$(VERSION) --build-arg COMMIT=$(COMMIT) \
		--build-arg DATE=$(DATE) -t megooci/agent:$(VERSION) .

snapshot:
	goreleaser release --clean --snapshot --skip=publish

clean:
	rm -rf bin dist
