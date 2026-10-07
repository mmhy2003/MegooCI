package executor

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/megooci/megooci-agent/internal/protocol"
)

const (
	defaultHTTPRequestTimeoutSec = 30
	maxHTTPRequestTimeoutSec     = 300
	maxHTTPRequestRetries        = 5
	// httpResponseReadLimit bounds how much of a response body is read at all.
	httpResponseReadLimit = 64 * 1024
	// httpResponseLogChars bounds how much of it is written to the build log.
	httpResponseLogChars = 2000
	// Values shorter than this are not masked: replacing them would mangle
	// ordinary output without hiding anything meaningful.
	httpMaskMinLen = 4
	// A space-separated part of a header value ("Bearer <token>") is masked
	// on its own when it is at least this long.
	httpMaskMinPartLen = 8
)

var httpRequestMethods = []string{"GET", "POST", "PUT", "PATCH", "DELETE"}

// httpRequestBackoff is the wait before retry n (1-based): 1s, 2s, 4s, ...
// A variable so tests can shorten it.
var httpRequestBackoff = func(retry int) time.Duration {
	return time.Duration(1<<uint(retry-1)) * time.Second
}

// httpRequestSpec is a validated http_request step configuration.
type httpRequestSpec struct {
	Method    string
	URL       *url.URL
	RawURL    string
	Headers   map[string]string
	Body      []byte
	Timeout   time.Duration
	Retries   int
	Expect    []int // empty means "any 2xx"
	VerifyTLS bool
	// secrets are the strings to hide in anything written to the build log,
	// longest first. Built once by buildHTTPMaskList.
	secrets []string
}

// Header values that are never credentials. Masking them would only mangle
// ordinary response text such as "application/json".
var httpNonSecretHeaders = map[string]bool{
	"accept":           true,
	"accept-encoding":  true,
	"accept-language":  true,
	"content-encoding": true,
	"content-length":   true,
	"content-type":     true,
	"user-agent":       true,
}

// configNumber reads a numeric config value. JSON numbers arrive as float64
// after the controller round-trip; ints appear in tests and local callers.
func configNumber(cfg map[string]interface{}, key string) (value float64, present bool, ok bool) {
	raw, exists := cfg[key]
	if !exists || raw == nil {
		return 0, false, true
	}
	switch v := raw.(type) {
	case float64:
		return v, true, true
	case int:
		return float64(v), true, true
	case int64:
		return float64(v), true, true
	}
	return 0, true, false
}

func isWholeNumber(v float64) bool { return v == float64(int64(v)) }

func isHTTPStatus(v float64) bool { return isWholeNumber(v) && v >= 100 && v <= 599 }

// parseHTTPRequestConfig validates the step config and builds the request
// description. Error messages never include the URL, a header value or the
// body: any of them may be a secret.
func parseHTTPRequestConfig(cfg map[string]interface{}) (*httpRequestSpec, error) {
	spec := &httpRequestSpec{
		Method:    "POST",
		Timeout:   defaultHTTPRequestTimeoutSec * time.Second,
		VerifyTLS: true,
	}

	rawURL := strings.TrimSpace(configStr(cfg, "url"))
	if rawURL == "" {
		return nil, errors.New("http_request: missing 'url'. If it comes from a secret, verify the secret exists and is in scope for this pipeline")
	}
	parsed, err := url.Parse(rawURL)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		// Deliberately not wrapping err: url.Parse errors quote the URL.
		return nil, errors.New("http_request: 'url' must be an absolute http:// or https:// URL")
	}
	spec.URL = parsed
	spec.RawURL = rawURL

	if raw, exists := cfg["method"]; exists && raw != nil {
		method := strings.ToUpper(strings.TrimSpace(configStr(cfg, "method")))
		allowed := false
		for _, m := range httpRequestMethods {
			if m == method {
				allowed = true
			}
		}
		if !allowed {
			return nil, fmt.Errorf("http_request: 'method' must be one of: %s", strings.Join(httpRequestMethods, ", "))
		}
		spec.Method = method
	}

	if raw, exists := cfg["headers"]; exists && raw != nil {
		if _, isMap := raw.(map[string]interface{}); !isMap {
			return nil, errors.New("http_request: 'headers' must be a mapping")
		}
		spec.Headers = configStrMap(cfg, "headers")
		for name, value := range spec.Headers {
			// A secret pasted with a trailing newline is common; HTTP does
			// not allow surrounding whitespace in a header value anyway.
			value = strings.TrimSpace(value)
			if strings.ContainsAny(value, "\r\n") {
				return nil, fmt.Errorf("http_request: header '%s' contains a line break", name)
			}
			spec.Headers[name] = value
		}
	}

	jsonValue, hasJSON := cfg["json"]
	bodyValue, hasBody := cfg["body"]
	if hasJSON && hasBody {
		return nil, errors.New("http_request: use either 'json' or 'body', not both")
	}
	if hasJSON {
		switch jsonValue.(type) {
		case map[string]interface{}, []interface{}:
		default:
			return nil, errors.New("http_request: 'json' must be a mapping or a list")
		}
		// An Encoder rather than json.Marshal so that <, > and & are sent
		// as written instead of as <-style escapes.
		var encoded bytes.Buffer
		encoder := json.NewEncoder(&encoded)
		encoder.SetEscapeHTML(false)
		if err := encoder.Encode(jsonValue); err != nil {
			return nil, errors.New("http_request: 'json' could not be encoded")
		}
		spec.Body = bytes.TrimRight(encoded.Bytes(), "\n")
		if !hasHeader(spec.Headers, "Content-Type") {
			if spec.Headers == nil {
				spec.Headers = map[string]string{}
			}
			spec.Headers["Content-Type"] = "application/json"
		}
	}
	if hasBody {
		text, isString := bodyValue.(string)
		if !isString {
			return nil, errors.New("http_request: 'body' must be a string")
		}
		spec.Body = []byte(text)
	}

	if v, present, ok := configNumber(cfg, "timeout"); present {
		if !ok || v <= 0 || v > maxHTTPRequestTimeoutSec {
			return nil, fmt.Errorf("http_request: 'timeout' must be a number of seconds greater than 0 and at most %d", maxHTTPRequestTimeoutSec)
		}
		spec.Timeout = time.Duration(v * float64(time.Second))
	}

	if v, present, ok := configNumber(cfg, "retries"); present {
		if !ok || !isWholeNumber(v) || v < 0 || v > maxHTTPRequestRetries {
			return nil, fmt.Errorf("http_request: 'retries' must be a whole number from 0 to %d", maxHTTPRequestRetries)
		}
		spec.Retries = int(v)
	}

	if raw, exists := cfg["expect_status"]; exists && raw != nil {
		badExpect := errors.New("http_request: 'expect_status' must be a status code (100-599) or a non-empty list of them")
		if list, isList := raw.([]interface{}); isList {
			if len(list) == 0 {
				return nil, badExpect
			}
			for _, item := range list {
				v, _, ok := configNumber(map[string]interface{}{"v": item}, "v")
				if !ok || !isHTTPStatus(v) {
					return nil, badExpect
				}
				spec.Expect = append(spec.Expect, int(v))
			}
		} else {
			v, _, ok := configNumber(cfg, "expect_status")
			if !ok || !isHTTPStatus(v) {
				return nil, badExpect
			}
			spec.Expect = []int{int(v)}
		}
	}

	if raw, exists := cfg["verify_tls"]; exists && raw != nil {
		verify, isBool := raw.(bool)
		if !isBool {
			return nil, errors.New("http_request: 'verify_tls' must be true or false")
		}
		spec.VerifyTLS = verify
	}

	spec.secrets = buildHTTPMaskList(spec)
	return spec, nil
}

// isPlainScalar reports whether a header value is a boolean or a number,
// which cannot be a credential.
func isPlainScalar(value string) bool {
	if value == "true" || value == "false" {
		return true
	}
	_, err := strconv.ParseFloat(value, 64)
	return err == nil
}

// buildHTTPMaskList collects every string that must not reach the build
// log if the receiver, a proxy or an error message echoes it back: the URL
// in each form a server might print it, its path segments and query values,
// its credentials, and the header values.
func buildHTTPMaskList(spec *httpRequestSpec) []string {
	seen := map[string]bool{}
	var list []string
	add := func(value string, minLen int) {
		if len(value) >= minLen && !seen[value] {
			seen[value] = true
			list = append(list, value)
		}
	}

	u := spec.URL
	// Whole-URL forms and anything that contains a path separator.
	for _, form := range []string{spec.RawURL, u.String(), u.RequestURI(), u.Path, u.EscapedPath(), u.RawQuery} {
		add(form, httpMaskMinLen)
	}
	for _, path := range []string{u.Path, u.EscapedPath()} {
		for _, segment := range strings.Split(path, "/") {
			add(segment, httpMaskMinPartLen)
		}
	}
	for _, values := range u.Query() {
		for _, value := range values {
			if !isPlainScalar(value) {
				add(value, httpMaskMinLen)
			}
		}
	}
	if u.User != nil {
		if password, ok := u.User.Password(); ok {
			add(password, httpMaskMinLen)
			credentials := u.User.Username() + ":" + password
			add(credentials, httpMaskMinLen)
			// The client turns URL credentials into a Basic header.
			add(base64.StdEncoding.EncodeToString([]byte(credentials)), httpMaskMinLen)
		}
	}

	for name, value := range spec.Headers {
		if httpNonSecretHeaders[strings.ToLower(name)] || isPlainScalar(value) {
			continue
		}
		add(value, httpMaskMinLen)
		if strings.Contains(value, " ") {
			for _, part := range strings.Fields(value) {
				add(part, httpMaskMinPartLen)
			}
		}
	}

	// Longest first: when one value contains another, replacing the short
	// one first would leave the rest of the long one in the log.
	sort.SliceStable(list, func(i, j int) bool { return len(list[i]) > len(list[j]) })
	return list
}

func hasHeader(headers map[string]string, name string) bool {
	for key := range headers {
		if strings.EqualFold(key, name) {
			return true
		}
	}
	return false
}

// displayURL is the only form of the URL written to the build log: scheme
// and host. The path, query and user-info often carry the secret.
func (s *httpRequestSpec) displayURL() string {
	return s.URL.Scheme + "://" + s.URL.Host + "/…"
}

func (s *httpRequestSpec) statusExpected(status int) bool {
	if len(s.Expect) == 0 {
		return status >= 200 && status <= 299
	}
	for _, want := range s.Expect {
		if want == status {
			return true
		}
	}
	return false
}

func (s *httpRequestSpec) expectedText() string {
	if len(s.Expect) == 0 {
		return "2xx"
	}
	parts := make([]string, len(s.Expect))
	for i, code := range s.Expect {
		parts[i] = fmt.Sprintf("%d", code)
	}
	return strings.Join(parts, ", ")
}

// mask replaces the request's URL and header values wherever they appear in
// text, in case the receiver (or an error message) echoes them back.
func (s *httpRequestSpec) mask(text string) string {
	for _, secret := range s.secrets {
		text = strings.ReplaceAll(text, secret, "***")
	}
	return text
}

// sanitizeLogText replaces control characters other than newline and tab.
// A response may be binary, and the controller's database cannot store NUL.
func sanitizeLogText(text string) string {
	return strings.Map(func(r rune) rune {
		if r == '\n' || r == '\t' {
			return r
		}
		if r < 0x20 || r == 0x7f {
			return '�'
		}
		return r
	}, text)
}

func newHTTPRequestClient(spec *httpRequestSpec) (*http.Client, *http.Transport) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if !spec.VerifyTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec // opt-in via verify_tls: false
	}
	client := &http.Client{
		Transport: transport,
		// Never follow redirects: that could resend credentials to another
		// address, and a redirected POST often becomes a GET.
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return client, transport
}

type httpAttemptResult struct {
	Status     int
	StatusText string
	Body       []byte
	Elapsed    time.Duration
}

// sendHTTPRequest performs one attempt, bounded by the step timeout.
func sendHTTPRequest(ctx context.Context, client *http.Client, spec *httpRequestSpec) (*httpAttemptResult, error) {
	attemptCtx, cancel := context.WithTimeout(ctx, spec.Timeout)
	defer cancel()

	var body io.Reader
	if spec.Body != nil {
		body = bytes.NewReader(spec.Body)
	}
	req, err := http.NewRequestWithContext(attemptCtx, spec.Method, spec.URL.String(), body)
	if err != nil {
		return nil, errors.New("could not build the request")
	}
	for name, value := range spec.Headers {
		req.Header.Set(name, value)
	}

	started := time.Now()
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, httpResponseReadLimit))
	return &httpAttemptResult{
		Status:     resp.StatusCode,
		StatusText: resp.Status,
		Body:       data,
		Elapsed:    time.Since(started),
	}, nil
}

// describeHTTPError renders a transport error without the URL. The standard
// client wraps errors as `Post "<full url>": <cause>`; only the cause is kept.
func describeHTTPError(err error, spec *httpRequestSpec) string {
	var urlErr *url.Error
	if errors.As(err, &urlErr) && urlErr.Err != nil {
		err = urlErr.Err
	}
	message := sanitizeLogText(spec.mask(err.Error()))
	if errors.Is(err, context.DeadlineExceeded) {
		return fmt.Sprintf("timed out after %s", spec.Timeout)
	}
	if strings.Contains(message, "x509:") {
		message += " (set 'verify_tls: false' to accept a self-signed certificate)"
	}
	return message
}

// runHTTPRequest handles http_request steps natively: one HTTP request sent
// from the agent, with optional retries. The URL path, request headers and
// request body are never written to the build log.
func (l *Local) runHTTPRequest(ctx context.Context, step Step, logs chan<- LogLine) Result {
	emit := func(stream, text string) {
		select {
		case logs <- LogLine{Stream: stream, Content: text}:
		case <-ctx.Done():
		}
	}
	fail := func(err error) Result {
		emit(protocol.StreamStderr, err.Error()+"\n")
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}
	cancelled := func() Result {
		return Result{ExitCode: -1, Status: protocol.StatusCancelled, Err: ctx.Err()}
	}

	spec, err := parseHTTPRequestConfig(step.Config)
	if err != nil {
		return fail(err)
	}
	if ctx.Err() != nil {
		return cancelled()
	}

	client, transport := newHTTPRequestClient(spec)
	defer transport.CloseIdleConnections()

	if !spec.VerifyTLS {
		emit(protocol.StreamStderr, "Warning: TLS certificate verification is disabled for this request (verify_tls: false)\n")
	}

	attempts := spec.Retries + 1
	var lastErr error
	for attempt := 1; attempt <= attempts; attempt++ {
		emit(protocol.StreamStdout, fmt.Sprintf("%s %s (attempt %d of %d)\n", spec.Method, spec.displayURL(), attempt, attempts))

		result, err := sendHTTPRequest(ctx, client, spec)
		if ctx.Err() != nil {
			return cancelled()
		}

		retryable := false
		if err != nil {
			lastErr = fmt.Errorf("http_request: request failed: %s", describeHTTPError(err, spec))
			retryable = true
		} else {
			emit(protocol.StreamStdout, fmt.Sprintf("%s in %d ms\n", result.StatusText, result.Elapsed.Milliseconds()))
			logHTTPResponseBody(result.Body, spec, emit)
			if spec.statusExpected(result.Status) {
				return Result{ExitCode: 0, Status: protocol.StatusSuccess}
			}
			note := ""
			if result.Status >= 300 && result.Status <= 399 {
				note = "; redirects are not followed"
			}
			lastErr = fmt.Errorf("http_request: unexpected status %d (expected %s%s)", result.Status, spec.expectedText(), note)
			retryable = result.Status >= 500 || result.Status == http.StatusTooManyRequests
		}

		if !retryable || attempt == attempts {
			break
		}
		wait := httpRequestBackoff(attempt)
		emit(protocol.StreamStderr, fmt.Sprintf("%s; retrying in %s\n", lastErr, wait))
		select {
		case <-time.After(wait):
		case <-ctx.Done():
			return cancelled()
		}
	}
	return fail(lastErr)
}

// logHTTPResponseBody writes the start of the response body to the build log,
// with the request's own URL and header values masked.
func logHTTPResponseBody(body []byte, spec *httpRequestSpec, emit func(stream, text string)) {
	text := strings.TrimRight(strings.ToValidUTF8(string(body), "�"), "\r\n")
	if strings.TrimSpace(text) == "" {
		return
	}
	// Carriage returns (CRLF line ends, progress output) become line breaks
	// before control characters are replaced.
	text = strings.NewReplacer("\r\n", "\n", "\r", "\n").Replace(spec.mask(text))
	text = sanitizeLogText(text)
	runes := []rune(text)
	if len(runes) > httpResponseLogChars {
		text = string(runes[:httpResponseLogChars])
		emit(protocol.StreamStdout, fmt.Sprintf("Response (first %d characters):\n", httpResponseLogChars))
	} else {
		emit(protocol.StreamStdout, "Response:\n")
	}
	for _, line := range strings.Split(text, "\n") {
		emit(protocol.StreamStdout, strings.TrimRight(line, "\r")+"\n")
	}
}
