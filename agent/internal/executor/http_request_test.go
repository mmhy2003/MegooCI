package executor

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// recordedRequest is what a test server saw.
type recordedRequest struct {
	Method string
	Path   string
	Header http.Header
	Body   string
}

// recordingServer answers every request with the given handler and records
// what it received.
func recordingServer(t *testing.T, handler func(w http.ResponseWriter, r *http.Request, n int)) (*httptest.Server, func() []recordedRequest) {
	t.Helper()
	var mu sync.Mutex
	var seen []recordedRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		seen = append(seen, recordedRequest{Method: r.Method, Path: r.URL.RequestURI(), Header: r.Header.Clone(), Body: string(body)})
		n := len(seen)
		mu.Unlock()
		handler(w, r, n)
	}))
	t.Cleanup(srv.Close)
	return srv, func() []recordedRequest {
		mu.Lock()
		defer mu.Unlock()
		return append([]recordedRequest(nil), seen...)
	}
}

func okHandler(w http.ResponseWriter, _ *http.Request, _ int) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok": true}`))
}

// fastBackoff removes the wait between retries for the duration of a test.
func fastBackoff(t *testing.T, d time.Duration) {
	t.Helper()
	original := httpRequestBackoff
	httpRequestBackoff = func(int) time.Duration { return d }
	t.Cleanup(func() { httpRequestBackoff = original })
}

// runHTTP runs an http_request step and returns its result and log output.
func runHTTP(t *testing.T, ctx context.Context, cfg map[string]interface{}) (Result, string) {
	t.Helper()
	l := NewLocal(Options{})
	logs := make(chan LogLine, 256)
	stop := drainLogs(logs)
	res := l.runHTTPRequest(ctx, Step{StepType: "http_request", Config: cfg}, logs)
	return res, stop()
}

func TestHTTPRequestSendsJSONBody(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": srv.URL + "/hooks/abc",
		"json": map[string]interface{}{
			"text":  "Build <42> & done — ünïcode",
			"count": float64(3),
			"ok":    true,
			"tags":  []interface{}{"ci", "prod"},
		},
	})

	if res.Status != "success" || res.ExitCode != 0 {
		t.Fatalf("result = %+v, logs:\n%s", res, out)
	}
	reqs := seen()
	if len(reqs) != 1 {
		t.Fatalf("server saw %d requests, want 1", len(reqs))
	}
	if reqs[0].Method != "POST" {
		t.Errorf("method = %q, want POST (the default)", reqs[0].Method)
	}
	if got := reqs[0].Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	var decoded map[string]interface{}
	if err := json.Unmarshal([]byte(reqs[0].Body), &decoded); err != nil {
		t.Fatalf("body is not JSON: %v (%q)", err, reqs[0].Body)
	}
	if decoded["text"] != "Build <42> & done — ünïcode" || decoded["count"] != float64(3) || decoded["ok"] != true {
		t.Errorf("decoded body = %#v", decoded)
	}
	if !strings.Contains(reqs[0].Body, "<42> &") {
		t.Errorf("body should carry <, > and & as written, got %q", reqs[0].Body)
	}
}

func TestHTTPRequestJSONListBody(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":  srv.URL,
		"json": []interface{}{"a", float64(1)},
	})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if got := seen()[0].Body; got != `["a",1]` {
		t.Errorf("body = %q", got)
	}
}

func TestHTTPRequestKeepsExplicitContentTypeWithJSON(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"headers": map[string]interface{}{"content-type": "application/vnd.api+json"},
		"json":    map[string]interface{}{"a": "b"},
	})

	if got := seen()[0].Header.Values("Content-Type"); len(got) != 1 || got[0] != "application/vnd.api+json" {
		t.Errorf("Content-Type = %v, want only the explicit one", got)
	}
}

func TestHTTPRequestSendsRawBodyUnchanged(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)
	raw := "<deploy>\n  <build>42</build>\n</deploy>\n"

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"method":  "put",
		"headers": map[string]interface{}{"Content-Type": "application/xml", "X-Attempt": float64(2), "X-Dry": true},
		"body":    raw,
	})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	req := seen()[0]
	if req.Method != "PUT" {
		t.Errorf("method = %q, want PUT", req.Method)
	}
	if req.Body != raw {
		t.Errorf("body = %q, want %q", req.Body, raw)
	}
	if req.Header.Get("Content-Type") != "application/xml" {
		t.Errorf("Content-Type = %q", req.Header.Get("Content-Type"))
	}
	if req.Header.Get("X-Attempt") != "2" || req.Header.Get("X-Dry") != "true" {
		t.Errorf("non-string header values not sent as strings: %v", req.Header)
	}
}

func TestHTTPRequestRawBodyGetsNoContentType(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "body": "plain"})

	if got := seen()[0].Header.Get("Content-Type"); got != "" {
		t.Errorf("Content-Type = %q, want none", got)
	}
}

func TestHTTPRequestWithoutBody(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "method": "GET"})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if req := seen()[0]; req.Method != "GET" || req.Body != "" {
		t.Errorf("request = %+v", req)
	}
}

func TestHTTPRequestNon2xxFailsWithoutRetry(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		http.Error(w, "no such hook", http.StatusNotFound)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "retries": float64(3)})

	if res.Status != "failed" || res.ExitCode != 1 {
		t.Fatalf("result = %+v", res)
	}
	if len(seen()) != 1 {
		t.Errorf("a 404 must not be retried; server saw %d requests", len(seen()))
	}
	if !strings.Contains(out, "unexpected status 404 (expected 2xx)") {
		t.Errorf("logs should state the unexpected status, got:\n%s", out)
	}
	if !strings.Contains(out, "no such hook") {
		t.Errorf("logs should show the response body, got:\n%s", out)
	}
}

func TestHTTPRequestExpectStatusAcceptsListedNon2xx(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusConflict)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":           srv.URL,
		"expect_status": []interface{}{float64(200), float64(409)},
	})

	if res.Status != "success" {
		t.Errorf("status = %q, want success for a listed 409", res.Status)
	}
}

func TestHTTPRequestExpectStatusRejectsUnlisted2xx(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusOK)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":           srv.URL,
		"expect_status": float64(202),
	})

	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if !strings.Contains(out, "unexpected status 200 (expected 202)") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestExpectedServerErrorIsSuccessNotRetried(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusServiceUnavailable)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":           srv.URL,
		"retries":       float64(2),
		"expect_status": float64(503),
	})

	if res.Status != "success" || len(seen()) != 1 {
		t.Errorf("status = %q after %d requests, want success after 1", res.Status, len(seen()))
	}
}

func TestHTTPRequestRetriesServerErrorThenSucceeds(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, n int) {
		if n == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"retries": float64(1),
		"json":    map[string]interface{}{"n": float64(1)},
	})

	if res.Status != "success" {
		t.Fatalf("status = %q, logs:\n%s", res.Status, out)
	}
	reqs := seen()
	if len(reqs) != 2 {
		t.Fatalf("server saw %d requests, want 2", len(reqs))
	}
	if reqs[0].Body != reqs[1].Body || reqs[1].Body != `{"n":1}` {
		t.Errorf("the body must be re-sent in full on retry: %q then %q", reqs[0].Body, reqs[1].Body)
	}
	if !strings.Contains(out, "attempt 1 of 2") || !strings.Contains(out, "attempt 2 of 2") || !strings.Contains(out, "retrying in") {
		t.Errorf("logs should show both attempts and the retry, got:\n%s", out)
	}
}

func TestHTTPRequestServerErrorWithoutRetriesFails(t *testing.T) {
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusBadGateway)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "failed" || len(seen()) != 1 {
		t.Errorf("status = %q after %d requests, want failed after 1", res.Status, len(seen()))
	}
}

func TestHTTPRequestRetriesTooManyRequests(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, n int) {
		if n < 3 {
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "retries": float64(2)})

	if res.Status != "success" || len(seen()) != 3 {
		t.Errorf("status = %q after %d requests, want success after 3", res.Status, len(seen()))
	}
}

func TestHTTPRequestGivesUpAfterAllRetries(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusInternalServerError)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "retries": float64(2)})

	if res.Status != "failed" || len(seen()) != 3 {
		t.Errorf("status = %q after %d requests, want failed after 3", res.Status, len(seen()))
	}
	if !strings.Contains(out, "unexpected status 500") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestTimesOut(t *testing.T) {
	release := make(chan struct{})
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	})
	defer close(release)

	started := time.Now()
	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "timeout": 0.2})

	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if elapsed := time.Since(started); elapsed > 5*time.Second {
		t.Errorf("timeout was not applied: took %s", elapsed)
	}
	if !strings.Contains(out, "timed out after 200ms") {
		t.Errorf("logs should name the timeout, got:\n%s", out)
	}
}

func TestHTTPRequestConnectionRefusedIsRetried(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	deadURL := srv.URL + "/secret-path-token"
	srv.Close()

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": deadURL, "retries": float64(1)})

	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if !strings.Contains(out, "attempt 2 of 2") {
		t.Errorf("a network error should be retried, got:\n%s", out)
	}
	if !strings.Contains(out, "request failed") {
		t.Errorf("logs = %s", out)
	}
	if strings.Contains(out, "secret-path-token") {
		t.Errorf("a network error must not reveal the URL path:\n%s", out)
	}
}

func TestHTTPRequestCancelledBeforeSending(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	res, _ := runHTTP(t, ctx, map[string]interface{}{"url": srv.URL})

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	if len(seen()) != 0 {
		t.Errorf("nothing should be sent after cancellation; server saw %d requests", len(seen()))
	}
}

func TestHTTPRequestCancelledWhileWaitingForResponse(t *testing.T) {
	arrived := make(chan struct{})
	var once sync.Once
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		once.Do(func() { close(arrived) })
		<-r.Context().Done()
	})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		<-arrived
		cancel()
	}()

	started := time.Now()
	res, _ := runHTTP(t, ctx, map[string]interface{}{"url": srv.URL, "timeout": float64(60)})

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	if elapsed := time.Since(started); elapsed > 10*time.Second {
		t.Errorf("cancellation did not abort the request: took %s", elapsed)
	}
}

func TestHTTPRequestCancelledDuringRetryWait(t *testing.T) {
	fastBackoff(t, time.Minute)
	var hits int32
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusInternalServerError)
	})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		for atomic.LoadInt32(&hits) == 0 {
			time.Sleep(5 * time.Millisecond)
		}
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()

	started := time.Now()
	res, _ := runHTTP(t, ctx, map[string]interface{}{"url": srv.URL, "retries": float64(3)})

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	if elapsed := time.Since(started); elapsed > 10*time.Second {
		t.Errorf("cancellation did not interrupt the retry wait: took %s", elapsed)
	}
}

func TestHTTPRequestDoesNotFollowRedirects(t *testing.T) {
	var targetHits int32
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		atomic.AddInt32(&targetHits, 1)
	}))
	defer target.Close()
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		http.Redirect(w, r, target.URL, http.StatusFound)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"headers": map[string]interface{}{"Authorization": "Bearer redirect-test-token"},
	})

	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if atomic.LoadInt32(&targetHits) != 0 {
		t.Errorf("the redirect target was contacted %d times", targetHits)
	}
	if !strings.Contains(out, "unexpected status 302 (expected 2xx; redirects are not followed)") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestSelfSignedCertificate(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})
	if res.Status != "failed" {
		t.Errorf("default: status = %q, want failed for a self-signed certificate", res.Status)
	}
	if !strings.Contains(out, "verify_tls: false") {
		t.Errorf("the failure should point to verify_tls, got:\n%s", out)
	}

	res, out = runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "verify_tls": false})
	if res.Status != "success" {
		t.Errorf("verify_tls false: status = %q, logs:\n%s", res.Status, out)
	}
	if !strings.Contains(out, "certificate verification is disabled") {
		t.Errorf("disabling verification should be logged, got:\n%s", out)
	}
}

func TestHTTPRequestLogsNeverContainSecrets(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		// A receiver that echoes what it was sent.
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("auth=" + r.Header.Get("Authorization") + " token=tok-9f8e7d6c5b4a key=" + r.Header.Get("X-Api-Key")))
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": srv.URL + "/services/T000/B000/path-secret-xyz?sig=query-secret-abc",
		"headers": map[string]interface{}{
			"Authorization": "Bearer tok-9f8e7d6c5b4a",
			"X-Api-Key":     "key-1122334455",
		},
		"json": map[string]interface{}{"password": "body-secret-777"},
	})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	for _, secret := range []string{"path-secret-xyz", "query-secret-abc", "tok-9f8e7d6c5b4a", "key-1122334455", "body-secret-777"} {
		if strings.Contains(out, secret) {
			t.Errorf("logs contain %q:\n%s", secret, out)
		}
	}
	host := strings.TrimPrefix(srv.URL, "http://")
	if !strings.Contains(out, "POST http://"+host+"/… (attempt 1 of 1)") {
		t.Errorf("logs should show the method, scheme and host only, got:\n%s", out)
	}
	if !strings.Contains(out, "200 OK in ") || !strings.Contains(out, "auth=*** token=*** key=***") {
		t.Errorf("logs should show the status and the masked response, got:\n%s", out)
	}
}

func TestHTTPRequestURLUserInfoIsNotLogged(t *testing.T) {
	srv, _ := recordingServer(t, okHandler)
	withUser := strings.Replace(srv.URL, "http://", "http://deploy:userinfo-secret@", 1)

	_, out := runHTTP(t, context.Background(), map[string]interface{}{"url": withUser})

	if strings.Contains(out, "userinfo-secret") || strings.Contains(out, "deploy:") {
		t.Errorf("logs contain URL credentials:\n%s", out)
	}
}

func TestHTTPRequestLongResponseIsTruncatedInLogs(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		_, _ = w.Write([]byte(strings.Repeat("x", 5000) + "THE-END"))
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if !strings.Contains(out, "Response (first 2000 characters):") {
		t.Errorf("logs should say the response was truncated, got %d bytes of logs", len(out))
	}
	if strings.Contains(out, "THE-END") || strings.Count(out, "x") > 2100 {
		t.Errorf("response was not truncated: %d x characters in the logs", strings.Count(out, "x"))
	}
}

func TestHTTPRequestHugeResponseDoesNotStallOrFlood(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		chunk := []byte(strings.Repeat("y", 64*1024))
		for i := 0; i < 80; i++ { // ~5 MiB
			if _, err := w.Write(chunk); err != nil {
				return
			}
		}
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if len(out) > 8*1024 {
		t.Errorf("logs are %d bytes for a 5 MiB response", len(out))
	}
}

func TestHTTPRequestEmptyResponsePrintsNoResponseSection(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusNoContent)
	})

	_, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if strings.Contains(out, "Response") {
		t.Errorf("an empty response should print no response section, got:\n%s", out)
	}
	if !strings.Contains(out, "204 No Content in ") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestBinaryResponseIsLoggedAsValidText(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		_, _ = w.Write([]byte{0xff, 0xfe, 'o', 'k', 0x80})
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if strings.ToValidUTF8(out, "") != out {
		t.Errorf("logs contain invalid UTF-8: %q", out)
	}
}

func TestHTTPRequestRejectsBadConfigBeforeSending(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	cases := []struct {
		name string
		cfg  map[string]interface{}
		want string
	}{
		{"missing url", map[string]interface{}{}, "missing 'url'"},
		{"empty url (unresolved secret)", map[string]interface{}{"url": "  "}, "missing 'url'"},
		{"no scheme", map[string]interface{}{"url": "hooks.example.com/x"}, "absolute http:// or https://"},
		{"other scheme", map[string]interface{}{"url": "ftp://example.com/x"}, "absolute http:// or https://"},
		{"no host", map[string]interface{}{"url": "https:///path-only"}, "absolute http:// or https://"},
		{"bad method", map[string]interface{}{"url": srv.URL, "method": "TRACE"}, "'method' must be one of"},
		{"headers not a mapping", map[string]interface{}{"url": srv.URL, "headers": "X: y"}, "'headers' must be a mapping"},
		{"json and body", map[string]interface{}{"url": srv.URL, "json": map[string]interface{}{}, "body": "x"}, "either 'json' or 'body'"},
		{"json scalar", map[string]interface{}{"url": srv.URL, "json": "text"}, "'json' must be a mapping or a list"},
		{"body not a string", map[string]interface{}{"url": srv.URL, "body": float64(5)}, "'body' must be a string"},
		{"timeout zero", map[string]interface{}{"url": srv.URL, "timeout": float64(0)}, "'timeout' must be"},
		{"timeout too large", map[string]interface{}{"url": srv.URL, "timeout": float64(301)}, "'timeout' must be"},
		{"timeout not a number", map[string]interface{}{"url": srv.URL, "timeout": "30"}, "'timeout' must be"},
		{"retries negative", map[string]interface{}{"url": srv.URL, "retries": float64(-1)}, "'retries' must be"},
		{"retries too many", map[string]interface{}{"url": srv.URL, "retries": float64(6)}, "'retries' must be"},
		{"retries fractional", map[string]interface{}{"url": srv.URL, "retries": 1.5}, "'retries' must be"},
		{"expect_status out of range", map[string]interface{}{"url": srv.URL, "expect_status": float64(99)}, "'expect_status' must be"},
		{"expect_status empty list", map[string]interface{}{"url": srv.URL, "expect_status": []interface{}{}}, "'expect_status' must be"},
		{"expect_status string", map[string]interface{}{"url": srv.URL, "expect_status": "200"}, "'expect_status' must be"},
		{"verify_tls not a boolean", map[string]interface{}{"url": srv.URL, "verify_tls": "no"}, "'verify_tls' must be"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res, out := runHTTP(t, context.Background(), tc.cfg)
			if res.Status != "failed" || res.ExitCode != 1 {
				t.Errorf("result = %+v, want failed", res)
			}
			if !strings.Contains(out, tc.want) {
				t.Errorf("logs should contain %q, got:\n%s", tc.want, out)
			}
		})
	}
	if n := len(seen()); n != 0 {
		t.Errorf("a rejected config must send nothing; server saw %d requests", n)
	}
}

func TestHTTPRequestBadURLErrorDoesNotEchoTheURL(t *testing.T) {
	_, out := runHTTP(t, context.Background(), map[string]interface{}{"url": "ftp://user:url-secret-123@example.com/x"})

	if strings.Contains(out, "url-secret-123") {
		t.Errorf("the rejected URL was written to the logs:\n%s", out)
	}
}

func TestHTTPRequestBackoffDoubles(t *testing.T) {
	want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second}
	for i, w := range want {
		if got := httpRequestBackoff(i + 1); got != w {
			t.Errorf("backoff before retry %d = %s, want %s", i+1, got, w)
		}
	}
}

func TestLocalRunDispatchesHTTPRequest(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)
	l := NewLocal(Options{Workdir: t.TempDir()})
	logs := make(chan LogLine, 64)
	var collected strings.Builder
	done := make(chan struct{})
	go func() {
		defer close(done)
		for line := range logs {
			collected.WriteString(line.Content)
		}
	}()

	res := l.Run(context.Background(), Step{
		BuildID:  "b1",
		StepID:   "s1",
		StepType: "http_request",
		Config:   map[string]interface{}{"url": srv.URL, "json": map[string]interface{}{"a": "b"}},
	}, logs)
	<-done

	if res.Status != "success" {
		t.Fatalf("status = %q, logs:\n%s", res.Status, collected.String())
	}
	if len(seen()) != 1 {
		t.Errorf("server saw %d requests, want 1", len(seen()))
	}
}

// ── review fixes ────────────────────────────────────────────────────────

func TestHTTPRequestEchoedPathAndQueryAreMasked(t *testing.T) {
	// Many servers put the request path in their error pages (Spring Boot's
	// default error JSON, Express's "Cannot POST /path", redirect pages).
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"status":404,"error":"Not Found","path":"` + r.URL.RequestURI() + `"}` + "\n" +
			"Cannot POST " + r.URL.Path + "\n" +
			"sig was " + r.URL.Query().Get("sig") + "\n" +
			`<a href="https://other.example` + r.URL.Path + `/">moved</a>`))
	})

	_, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": srv.URL + "/services/T000/B000/path-secret-xyz?sig=query-secret-abc&v=2",
	})

	for _, secret := range []string{"path-secret-xyz", "query-secret-abc", "/services/T000"} {
		if strings.Contains(out, secret) {
			t.Errorf("logs contain %q echoed by the receiver:\n%s", secret, out)
		}
	}
	if !strings.Contains(out, "unexpected status 404") || !strings.Contains(out, "Not Found") {
		t.Errorf("the rest of the response should still be readable, got:\n%s", out)
	}
}

func TestHTTPRequestEchoedURLCredentialsAreMasked(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		user, pass, _ := r.BasicAuth()
		_, _ = w.Write([]byte("header=" + r.Header.Get("Authorization") + " user=" + user + " pass=" + pass))
	})
	withUser := strings.Replace(srv.URL, "http://", "http://deploy-user:userinfo-secret-99@", 1)

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": withUser})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	basic := base64.StdEncoding.EncodeToString([]byte("deploy-user:userinfo-secret-99"))
	for _, secret := range []string{"userinfo-secret-99", basic} {
		if strings.Contains(out, secret) {
			t.Errorf("logs contain %q:\n%s", secret, out)
		}
	}
}

func TestHTTPRequestMaskReplacesLongestValueFirst(t *testing.T) {
	// One header value is a prefix of another. Masking the short one first
	// would leave the tail of the long one in the log.
	spec, err := parseHTTPRequestConfig(map[string]interface{}{
		"url": "https://example.com/x",
		"headers": map[string]interface{}{
			"X-User":        "deploy",
			"Authorization": "Bearer deploy-token-xyz987",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 200; i++ { // map iteration order is random
		if got := spec.mask("auth=Bearer deploy-token-xyz987 user=deploy"); strings.Contains(got, "token-xyz987") {
			t.Fatalf("run %d: part of the token survived masking: %q", i, got)
		}
	}
}

func TestHTTPRequestOrdinaryHeaderValuesAreNotMasked(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		_, _ = w.Write([]byte(`{"ok": true, "accepted": "application/json", "attempt": 2024}`))
	})

	_, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": srv.URL,
		"headers": map[string]interface{}{
			"X-Dry":     true,
			"X-Attempt": float64(2024),
			"Accept":    "application/json",
		},
		"json": map[string]interface{}{"a": "b"},
	})

	if !strings.Contains(out, `{"ok": true, "accepted": "application/json", "attempt": 2024}`) {
		t.Errorf("booleans, numbers and content types are not secrets and must stay readable, got:\n%s", out)
	}
}

func TestHTTPRequestTransportErrorEchoingThePathIsMasked(t *testing.T) {
	// A peer that is not an HTTP server and echoes the request line back
	// makes Go report a malformed response that quotes what it received.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				line, _ := bufio.NewReader(c).ReadString('\n')
				_, _ = c.Write([]byte(line))
			}(conn)
		}
	}()

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": "http://" + ln.Addr().String() + "/malformed-secret-path",
	})

	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if strings.Contains(out, "malformed-secret-path") {
		t.Errorf("a transport error revealed the URL path:\n%s", out)
	}
}

func TestHTTPRequestControlCharactersAreNotLogged(t *testing.T) {
	// A NUL byte in a log line cannot be stored by the server's database.
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		_, _ = w.Write([]byte("PK\x00\x01\x1b[31mred\x00\tcol\nline2\x7f"))
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	for _, r := range out {
		if r != '\n' && r != '\t' && (r < 0x20 || r == 0x7f) {
			t.Fatalf("logs contain control character %U:\n%q", r, out)
		}
	}
	if !strings.Contains(out, "\tcol") || !strings.Contains(out, "line2") {
		t.Errorf("tabs, newlines and text should survive, got:\n%q", out)
	}
}

func TestHTTPRequestHeaderValueWhitespaceIsTrimmed(t *testing.T) {
	// A token pasted into a secret with a trailing newline is very common.
	srv, seen := recordingServer(t, okHandler)

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"headers": map[string]interface{}{"Authorization": "  Bearer tok-with-newline-1234\r\n"},
		"retries": float64(2),
	})

	if res.Status != "success" {
		t.Fatalf("status = %q, logs:\n%s", res.Status, out)
	}
	if got := seen()[0].Header.Get("Authorization"); got != "Bearer tok-with-newline-1234" {
		t.Errorf("Authorization = %q", got)
	}
}

func TestHTTPRequestHeaderWithLineBreakFailsWithoutRetrying(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, okHandler)

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"headers": map[string]interface{}{"X-Token": "first-line-secret\nsecond-line-secret"},
		"retries": float64(3),
	})

	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if !strings.Contains(out, "header 'X-Token' contains a line break") {
		t.Errorf("logs should name the header, got:\n%s", out)
	}
	if strings.Contains(out, "first-line-secret") || strings.Contains(out, "attempt") {
		t.Errorf("the value must not be logged and nothing should be attempted:\n%s", out)
	}
	if len(seen()) != 0 {
		t.Errorf("server saw %d requests, want 0", len(seen()))
	}
}
