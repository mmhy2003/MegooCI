# On Windows, GNU Make defaults SHELL to sh.exe which is usually not on
# PATH. Detect this and fall back to cmd.exe so recipes actually execute.
ifeq ($(OS),Windows_NT)
  SHELL := cmd.exe
  .SHELLFLAGS := /C
endif

.PHONY: build build-all tidy test vet lint fmt docker snapshot release clean

BINARY      := megooci-agent
VERSION     ?= 0.1.0-dev
COMMIT      := $(shell git rev-parse --short HEAD 2>$(if $(filter cmd.exe,$(SHELL)),nul,/dev/null) || echo unknown)

ifeq ($(OS),Windows_NT)
  # Windows: use PowerShell for UTC timestamp since cmd.exe has no `date -u`.
  DATE := $(shell powershell -NoProfile -Command "[DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')")
else
  DATE := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
endif

LDFLAGS := -s -w \
	-X github.com/megooci/megooci-agent/internal/version.Version=$(VERSION) \
	-X github.com/megooci/megooci-agent/internal/version.Commit=$(COMMIT) \
	-X github.com/megooci/megooci-agent/internal/version.Date=$(DATE)

ifeq ($(OS),Windows_NT)
build:
	set CGO_ENABLED=0&& go build -trimpath -ldflags "$(LDFLAGS)" -o bin/$(BINARY).exe ./cmd/megooci-agent
else
build:
	CGO_ENABLED=0 go build -trimpath -ldflags "$(LDFLAGS)" \
		-o bin/$(BINARY) ./cmd/megooci-agent
endif

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

build-all:
	goreleaser build --clean --snapshot

snapshot:
	goreleaser release --clean --snapshot --skip=publish

release:
	goreleaser release --clean

ifeq ($(OS),Windows_NT)
clean:
	if exist bin rmdir /s /q bin
	if exist dist rmdir /s /q dist
else
clean:
	rm -rf bin dist
endif
