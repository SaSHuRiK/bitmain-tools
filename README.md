# bitmain L3 voltage tool

This version of jstefanop's set_voltage also allows to read out voltages only and gives better parseable output.

This tool allows voltage editing on Bitmain’s L3+ hashboards, it is provided free by the developer so if you find this useful and/or want me to update/provide new tools please donate to the below address. Thanks!

LTC: LQZpb8AqbggUmsdPKr28DzdNcRP7MJ8kEf
BTC: 1LeA29mLjTFCyB5J6Yiq4TFtAck2VDPUZf


————

Usage:

scp binary to /config directory in your unit’s controller…this is the only directly that gets saved on reboot on antminers

binary accepts two inputs in the format of:

./set_voltage [chain# 1-4] [voltage in hex]

bitmains voltage controller can be configured to change the 12v input roughly +/- 1v from 10v, and this is configurable via a hex range of 0x00-0xfe, with the default being set to the middle (0x80). Higher hex values (0x80-0xfe) will LOWER voltage, lower values (0x00-0x7f) will INCREASE voltage from the default. 

For example if you want to slightly decrease your voltage on chain #1 you would input:

./set_voltage 1 90

increments of 0x10 are good starting point to test a sweet spot for each board for a particular frequency. Lowering voltage until you get around 1 HW error per minute is usually a good reference “sweet spot”

If you only want to read out chain #1 just ommit the voltage:

./set_voltage 1

If you want to read out all chains' voltage, use:

./set_voltage

If you like this tool, send some coins to the original author jstefanop at above LTC/BTC addresses.
