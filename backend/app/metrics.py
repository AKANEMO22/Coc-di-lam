from collections import Counter


class Metrics:
    def __init__(self):
        self.requests: Counter = Counter()
        self.events: Counter = Counter()
        self.ws_active = 0

    def request(self, method: str, route: str, status: int) -> None:
        self.requests[(method, route, f"{status // 100}xx")] += 1

    def event(self, name: str) -> None:
        self.events[name] += 1

    def render(self) -> str:
        lines = ["# TYPE gateway_http_requests_total counter"]
        for (method, route, family), count in sorted(self.requests.items()):
            lines.append(f'gateway_http_requests_total{{method="{method}",route="{route}",status="{family}"}} {count}')
        lines.append("# TYPE gateway_events_total counter")
        for name, count in sorted(self.events.items()):
            lines.append(f'gateway_events_total{{event="{name}"}} {count}')
        lines += ["# TYPE gateway_ws_active gauge", f"gateway_ws_active {self.ws_active}"]
        return "\n".join(lines) + "\n"
