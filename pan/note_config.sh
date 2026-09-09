# client inside zone
ip addr add 192.168.3.10/24 dev eth1
ip link set eth1 up
ip route del default
ip route add default via 192.168.3.1

# client dmz zone
ip addr add 192.168.2.10/24 dev eth1
ip link set eth1 up
ip route del default
ip route add default via 192.168.2.1

# client wan zone
ip addr add 10.0.1.201/24 dev eth1
ip link set eth1 up
ip route del default
ip route add default via 10.0.1.2

# Palo Alto Firewall
configure
set network interface ethernet ethernet1/1 layer3 dhcp-client enable yes
set network interface ethernet ethernet1/1 layer3 dhcp-client create-default-route yes
set network profiles interface-management-profile allow-mgmt ping yes
set network profiles interface-management-profile allow-mgmt ssh yes
set network profiles interface-management-profile allow-mgmt telnet yes
set network profiles interface-management-profile allow-mgmt http yes
set network profiles interface-management-profile allow-mgmt https yes
set network profiles interface-management-profile allow-mgmt response-pages yes
set network interface ethernet ethernet1/1 layer3 interface-management-profile allow-mgmt
set zone outside network layer3 ethernet1/1
set network virtual-router default interface ethernet1/1
commit

# lệnh để cài các công cụ cần thiết cho lab (ping, curl, dig, mtr, tcpdump, ncat, ...)
apk add --no-cache iputils curl bind-tools mtr tcpdump nmap-ncat busybox-extras iperf3

# cách dùng
iputils → có lệnh ping
bind-tools → có dig, nslookup (test DNS)
mtr → traceroute nâng cao
tcpdump → bắt gói tin
nmap-ncat → có nc (netcat, test port)
busybox-extras → có telnet, traceroute
iperf3 → test băng thông/tốc độ mạng

# hướng dẫn
#1. Kiểm tra kết nối cơ bản (ping)
ping -c 4 8.8.8.8
ping -c 4 google.com

#2. Kiểm tra DNS
nslookup google.com
dig google.com
dig @8.8.8.8 google.com

#3. Kiểm tra route đi qua các hop nào (traceroute)
traceroute 8.8.8.8
# hoặc dùng mtr (traceroute real-time, trực quan hơn)
mtr 8.8.8.8

#4. Test HTTP/HTTPS
curl -I https://google.com
curl -v http://<IP-firewall-hoặc-server>
wget -qO- http://<IP>

#5. Test port cụ thể có mở không (rất hữu ích để test firewall policy)
nc -zv 10.0.1.199 443
nc -zv 10.0.1.199 22
#Cờ -z chỉ kiểm tra port mở/đóng, không gửi dữ liệu; -v hiện chi tiết.

#6. Xem địa chỉ IP, interface hiện tại
ip addr show
ip route show

#7. Test kết nối TCP thủ công (giả lập client/server)

Máy A (làm server, lắng nghe)
nc -lvp 8080

Máy B (làm client, kết nối tới)
nc -v 10.0.1.201 8080

#8. Bắt gói tin để debug traffic (khi test qua firewall)
tcpdump -i eth1 -n

#Chỉ bắt traffic ICMP
tcpdump -i eth1 icmp -n

#9. Kiểm tra ARP table
ip neigh show

#10. Test tốc độ/băng thông đơn giản (nếu cần)
#Server:
iperf3 -s

#Client:
iperf3 -c 10.0.1.201
#Ví dụ kịch bản test thực tế qua firewall

#############################################
# 1. Xem IP hiện tại
ip addr show eth1

# 2. Ping qua firewall ra ngoài
ping -c 4 8.8.8.8

# 3. Test DNS
nslookup google.com

# 4. Test HTTP/HTTPS có bị chặn không
curl -I https://google.com

# 5. Test port cụ thể (ví dụ SSH tới thiết bị khác trong lab)
nc -zv 10.0.1.199 22

