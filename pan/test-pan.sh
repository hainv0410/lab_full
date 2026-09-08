configure
set network interface ethernet ethernet1/1 layer3 ip 10.0.0.199/24
set network virtual-router default interface ethernet1/1
set network virtual-router default routing-table ip static-route default nexthop ip-address 10.0.1.2 destination 0.0.0.0/0
commit


Bước 2: Tạo Interface Management Profile cho phép ping + HTTPS
configure

set network profiles interface-management-profile allow-mgmt ping yes
set network profiles interface-management-profile allow-mgmt response-pages yes
set network profiles interface-management-profile allow-mgmt https yes
set network profiles interface-management-profile allow-mgmt http no

(đặt tên profile là allow-mgmt, bạn có thể đổi tên tùy ý)

Bước 3: Gán IP + zone + apply profile vừa tạo cho Ethernet1/1
set network interface ethernet ethernet1/1 layer3 ip 10.0.1.199/24
set network interface ethernet ethernet1/1 layer3 interface-management-profile allow-mgmt

set zone DMZ network layer3 ethernet1/1
set network virtual-router default interface ethernet1/1

(dùng zone tên DMZ, bạn đổi tên tùy theo lab của mình; lưu ý IP 10.0.1.199/24 để cùng dải với gateway 10.0.1.2 mà bạn nêu trước đó — nếu gateway thật khác, đổi lại cho đúng)

Bước 4: Thêm default route ra ngoài (nếu cần)
set network virtual-router default routing-table ip static-route default nexthop ip-address 10.0.1.2
set network virtual-router default routing-table ip static-route default destination 0.0.0.0/0
Bước 5: Tạo Security Policy cho phép traffic (nếu bạn cần cả traffic đi qua, không chỉ quản trị)

Nếu bạn chỉ cần ping/truy cập web tới chính interface (management access), bước 2-3 ở trên là đủ, không cần security policy vì management traffic tới chính interface được xử lý riêng, không đi qua security policy giữa các zone.

Nếu bạn cần traffic đi xuyên qua firewall (ví dụ máy sau Ethernet1/1 ra Internet), thì cần thêm:

set rulebase security rules allow-outbound from DMZ to any source any destination any application any service application-default action allow
Bước 6: Commit cấu hình
commit


sudo ip link add macvlan-host link ens160 type macvlan mode bridge
sudo ip addr add 10.0.1.250/24 dev macvlan-host
sudo ip link set macvlan-host up




######## DHCP ##########
configure
#Xóa IP tĩnh cũ (nếu trước đó đã đặt static IP)
delete network interface ethernet ethernet1/1 layer3 ip

#Bật DHCP client
set network interface ethernet ethernet1/1 layer3 dhcp-client enable yes
set network interface ethernet ethernet1/1 layer3 dhcp-client create-default-route yes

#Tạo Interface Management Profile cho phép đầy đủ dịch vụ
set network profiles interface-management-profile allow-mgmt ping yes
set network profiles interface-management-profile allow-mgmt ssh yes
set network profiles interface-management-profile allow-mgmt telnet yes
set network profiles interface-management-profile allow-mgmt http yes
set network profiles interface-management-profile allow-mgmt https yes
set network profiles interface-management-profile allow-mgmt response-pages yes

#Gán profile vào ethernet1/1
set network interface ethernet ethernet1/1 layer3 interface-management-profile allow-mgmt

#Đảm bảo interface thuộc đúng zone và virtual router
set zone DMZ network layer3 ethernet1/1
set network virtual-router default interface ethernet1/1
commit

# Kiểm tra
show interface ethernet1/1
show dhcp client state ethernet1/1
show network profiles interface-management-profile allow-mgmt


