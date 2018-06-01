#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------
# Copyright (c) 2018 Netcraft <src@netcraft.ch>
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. The name of the author may not be used to endorse or promote products
#    derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL
# THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# --------------------------------------------------------------------------
# l3plus_autotune.py: automatically tune undervolting on BM L3+/L3++
# $Id: l3plus_autotune.py,v 1.56 2018-06-01 13:22:48 obiwan Exp $
# --------------------------------------------------------------------------

# encode/decode trick with perl courtesy of:
# https://unix.stackexchange.com/questions/205635/convert-binary-mode-to-text-mode-and-the-reverse-option

# TODO:

# DONE:
# - skip selected chains that have to be tuned manually
# - cmd line options

#########
# IMPORTS
#########
import socket, json, sys, time, signal, tempfile, os, getopt
from datetime import datetime

try:
  import paramiko
except:
  print "paramiko module missing, please install with:"
  print " sudo apt-get install python-paramiko"
  sys.exit(1)

###########
# CONSTANTS
###########
API_PORT = 4028
# setvoltage binary
SETV_BIN = '/config/sv'
SETV_BIN_MD5 = '113ad2c06daac293386e28807ea35671'
REPEAT = 60
# array length based on above REPEAT rate
LEN5MIN = 5*60 / REPEAT
LEN10MIN = 10*60 / REPEAT
LEN15MIN = 15*60 / REPEAT
# max. history records to retain in memory (2days default)
HIST_MAX_LEN = 2880
# max acceptable errors/min
MAX_ERR_RATE = 0.2 # 0.25 / 15 errors per hour, 0.2 / 12/h, 0.1 / 6/h
MAX_VOLTAGE = '0x50'
#TUNE_REPEAT = 300 # retry tuning voltage every TUNE_REPEAT seconds
#for testing
TUNE_REPEAT = 300
# absolutr maximum cycles
MAX_CYCLE = 1200
# socket timeout
socket.setdefaulttimeout(10)


###############
# NET FUNCTIONS
###############
def get_minerstats(ip, port=API_PORT):
  """Get all stats from miner API"""
  try:
    json_resp = ""
    s = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    s.connect((ip, port))
    s.send(json.dumps({"command":'stats'}))
    while True:
      data = s.recv(1024)
      if not data:
        break
      json_resp += data
    s.close()
  except socket.error, e:
    print "Failed to connect to host:\n%s" %e
    sys.exit(1)
  try:
    if json_resp.find('Blissz v1.02"}') > -1:
      json_resp = json_resp.replace('Blissz v1.02"}', 'Blissz v1.02"},')
      #print "Blissz fw detected, fixing json output accordingly"
    else:
      json_resp = json_resp.replace('\x00','')
      json_resp = json_resp.replace("L3+\"}", "L3+\"},")
      #print "Bitmain fw detected, fixing json output accordingly"
    resp = json.loads(json_resp)
  except ValueError, e:
    print "Failed to decode json reply:\n%s\n" %e
    print json_resp
    sys.exit(1)
    ""
  
  miner_stats = {}
  miner_stats['err'] = []
  miner_stats['speed'] = []
  miner_stats['chainrate'] = []
  miner_stats['temp_pcb'] = []
  miner_stats['temp_chip'] = []
  miner_stats['asic_status'] = []
  for i in range (1,5):
    #print resp['STATS'][1]['chain_hw'+str(i)]
    miner_stats['err'].append( resp['STATS'][1]['chain_hw'+str(i)] )
    miner_stats['chainrate'].append( resp['STATS'][1]['chain_rate'+str(i)] ) 
    miner_stats['temp_pcb'].append( resp['STATS'][1]['temp'+str(i)] ) 
    miner_stats['temp_chip'].append( resp['STATS'][1]['temp2_'+str(i)] ) 
    miner_stats['asic_status'].append( resp['STATS'][1]['chain_acs'+str(i)] ) 
    
  miner_stats['speed'] = [resp['STATS'][1]['GHS av'], resp['STATS'][1]['GHS 5s']]
  miner_stats['uptime'] = resp['STATS'][1]['Elapsed']
  miner_stats['frequency'] = resp['STATS'][1]['frequency']
  miner_stats['device_error'] = resp['STATS'][1]['Device Hardware%']
  return miner_stats

def get_voltage(ip, chain=False):
  """Get voltage remotely, returns a list with errors of each of the 4 chains"""
  cur_voltage = []
  try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy( paramiko.AutoAddPolicy() )
    client.load_system_host_keys()
    client.connect(ip, port=22, username='root', password=admin_pw)
    stdin, stdout, stderr = client.exec_command(SETV_BIN)
    res = stdout.read()
    err = stderr.read()
  except socket.error, e:
    print "Failed to connect to %s via ssh:\n%s" %(ip, e)
    sys.exit(1)
  except paramiko.AuthenticationException, e:
    print "Authentication to %s failed:\n%s" %(ip, e)
    sys.exit(1)
  # a shell error ocurred on the miner:
  if len(err) > 0:
    if err.endswith(": not found\n"):
      print "%s binary not found on target miner, installing it first:" %SETV_BIN
      install_sv_bin(ip, admin_pw, os.path.split(__file__)[0] + '/sv.txt')
    else:
      print "Undefined errors occured fetching voltage settings from miner:\n%s" %err
      print "Aborting."
      sys.exit(1)
  for line in res.split('\n'):
    if line.strip().find("chain", 0) > -1:
      cur_voltage.append(line.split('=')[1].strip())
  client.close()  
  return cur_voltage


def set_voltage(ip, chain, voltage):
  """Set voltages remotely"""
  #print voltage, type(voltage), int(voltage,16)
  if int(voltage,16) <= int(MAX_VOLTAGE,16):
    print "Limiting voltage to MAX_VOLTAGE (%s)" %MAX_VOLTAGE
    voltage = MAX_VOLTAGE    
  elif int(voltage,16) > 254:
    print "Limiting undervolt to max 254 (0xfe)"
    voltage = '0xfe'
  this_voltage = []
  v_cmd = SETV_BIN + " " + str(chain) + " " + str(voltage)
  client = paramiko.SSHClient()
  client.set_missing_host_key_policy( paramiko.AutoAddPolicy() )
  client.load_system_host_keys()
  client.connect(ip, port=22, username='root', password=admin_pw)
  stdin, stdout, stderr = client.exec_command(v_cmd)
  res = stdout.read()
  for line in res.split('\n'):
    if line.strip().find("voltage =", 0) > -1:
      this_voltage.append(line.split('=')[1].strip())
  client.close()  
  return this_voltage
  
def install_sv_bin(ip, pw, ascii_file):
  global install_flag
  if install_flag:
    print "We have already tried (and failed) to install %s binary on the miner.\nPlease investigate before procedding!"
    print "Exiting.."
    sys.exit(1)

  client = paramiko.SSHClient()
  client.set_missing_host_key_policy( paramiko.AutoAddPolicy() )
  client.load_system_host_keys()
  client.connect(ip, port=22, username='root', password=admin_pw)
  #i_cmd = "echo '%s' > /config/sv.asc" %txt
  i_cmd = "cat > /config/sv.asc"
  fh = open(ascii_file, 'r')
  txt = fh.read()
  fh.close()
  stdin, stdout, stderr = client.exec_command(i_cmd)
  stdin.write(txt)
  i_cmd = "perl -ape '$_=pack \"(H2)*\", @F' /config/sv.asc > %s" %SETV_BIN
  stdin, stdout, stderr = client.exec_command(i_cmd)
  res = stdout.read()
  err = stderr.read()
  if len(err) > 0:
    print "Something went wrong installing the %s binary, please check!" %SETV_BIN
    sys.exit(1)
  i_cmd = "rm /config/sv.asc && chmod 750 %s && md5sum %s" %(SETV_BIN, SETV_BIN)
  stdin, stdout, stderr = client.exec_command(i_cmd)
  res = stdout.read()
  err = stderr.read()
  if len(err) > 0:
    print "Something went wrong installing the %s binary, please check!" %SETV_BIN
    sys.exit(1)
  if res.split(" ")[0].strip() != SETV_BIN_MD5:
    print "MD5sum does not match:\n%s\nAborting" %res.split(" ")[0].strip()
    sys.exit(1)
  else:
    print "MD5sum [%s] matches, good." %res.split(" ")[0].strip()
  print "Binary %s successfully installed." %SETV_BIN
  install_flag = True
  client.close() 
  # Retry getting voltage 
  get_voltage(ip)
  
  
###################
# HISTORY FUNCTIONS
###################
def add_history(stats, voltage, ts):
  """Add records to history structure"""
  if len(stats['chainrate']) != 4 or len(voltage) != 4:
    print "Invalid boards read, aborted!"
    print len(stats), len(voltage)
    sys.exit(1)
  stats['voltage'] = voltage
  stats['timestamp'] = ts
  if not chain_hist.has_key(stats['frequency']):
    chain_hist[stats['frequency']] = []  
  chain_hist[stats['frequency']].append(stats)
  
def process_history(freq):
  """Process history structure"""
  global chain_hist
  min5_err = []
  min10_err = []
  min15_err = []
  min_err = []
  if len(chain_hist[freq]) < 2:
    chain_hist[freq][-1]['error_rate5'] = [0, 0, 0, 0]
    chain_hist[freq][-1]['error_rate10'] = [0, 0, 0, 0]
    chain_hist[freq][-1]['error_rate15'] = [0, 0, 0, 0]
    chain_hist[freq][-1]['error_rate'] = [0, 0, 0, 0]
    return
  else:
    arr_end = len(chain_hist[freq]) - 1

  for i in range(0,4):
    min5_errors = chain_hist[freq][arr_end]['err'][i] - chain_hist[freq][max( 0, (arr_end - LEN5MIN) )]['err'][i]
    min5_err.append(min5_errors)
    min10_errors = chain_hist[freq][arr_end]['err'][i] - chain_hist[freq][max(0, (arr_end - LEN10MIN))]['err'][i]
    min10_err.append(min10_errors)
    min15_errors = chain_hist[freq][arr_end]['err'][i] - chain_hist[freq][max(0, (arr_end - LEN15MIN))]['err'][i]
    min15_err.append(min15_errors)
    min_err.append(chain_hist[freq][-1]['err'][i] - chain_hist[freq][0]['err'][i])    

  arr_start5 = max(0, arr_end - LEN5MIN)
  arr_start10 = max(0, arr_end - LEN10MIN)
  arr_start15 = max(0, arr_end - LEN15MIN)
  timediff5 = chain_hist[freq][arr_end]['timestamp'] - chain_hist[freq][arr_start5]['timestamp']
  timediff10 = chain_hist[freq][arr_end]['timestamp'] - chain_hist[freq][arr_start10]['timestamp']
  timediff15 = chain_hist[freq][arr_end]['timestamp'] - chain_hist[freq][arr_start15]['timestamp']
  timediff = chain_hist[freq][-1]['timestamp'] - chain_hist[freq][0]['timestamp']

  #print "Arr_start, Arr_end:", arr_start5, arr_end, arr_start10, arr_end, arr_start15, arr_end
  #print "Arr_start (raw):", arr_end - (5*60)/REPEAT, arr_end - (10*60)/REPEAT, arr_end - (15*60)/REPEAT
  #print "Error time1, time2, time3, time:", timediff5, timediff10, timediff15, timediff
  
  f1 = lambda err: float(err) / timediff5*60
  f2 = lambda err: float(err) / timediff10*60
  f3 = lambda err: float(err) / timediff15*60
  f4 = lambda err: float(err) / timediff*60
  min5_avg = map(f1, min5_err)
  min10_avg = map(f2, min10_err)
  min15_avg = map(f3, min15_err)
  all_avg = map(f4, min_err)
  chain_hist[freq][-1]['error_rate5'] = min5_avg
  chain_hist[freq][-1]['error_rate10'] = min10_avg
  chain_hist[freq][-1]['error_rate15'] = min15_avg
  chain_hist[freq][-1]['error_rate'] = all_avg
  #print "Errors:", min5_err, min10_err, min15_err, min_err  
  temp_chip = chain_hist[freq][-1]['temp_chip']
  print "| %s [%s] |  %i  |  %i  |  %i  |  %i  |" %(miner_ip.ljust(12)[:12], freq, temp_chip[0], temp_chip[1], temp_chip[2], temp_chip[3])
  print "+ Current voltages   + %s + %s + %s + %s +" %(current_voltage[0], current_voltage[1], current_voltage[2], current_voltage[3])
  print "|Errors/min (5min)   | %.2f | %.2f | %.2f | %.2f | %i %i %i %i |" \
	%(min5_avg[0], min5_avg[1], min5_avg[2], min5_avg[3], min5_err[0], min5_err[1], min5_err[2], min5_err[3])
  print "|Errors/min (10min)  | %.2f | %.2f | %.2f | %.2f | %i %i %i %i |" \
	%(min10_avg[0], min10_avg[1], min10_avg[2], min10_avg[3], min10_err[0], min10_err[1], min10_err[2], min10_err[3])
  print "|Errors/min (15min)  | %.2f | %.2f | %.2f | %.2f | %i %i %i %i |" \
	%(min15_avg[0], min15_avg[1], min15_avg[2], min15_avg[3], min15_err[0], min15_err[1], min15_err[2], min15_err[3])
  print "|Errors/min (all)    | %.2f | %.2f | %.2f | %.2f | %i %i %i %i |" \
	%(all_avg[0], all_avg[1], all_avg[2], all_avg[3], min_err[0], min_err[1], min_err[2], min_err[3])
  

def voltage_history(freq, chain, voltage):
  """lookup if a result exists with same freq/voltage combination and return the error rate"""
  errors5 = []
  errors10 = []
  errors15 = []
  tested = False;
  for r in chain_hist[freq][:-(TUNE_REPEAT/REPEAT)]:
    if r['voltage'][chain] == voltage:
      errors5.append(r['error_rate5'][chain])
      errors10.append(r['error_rate10'][chain])
      errors15.append(r['error_rate15'][chain])
      tested = True
  if sum(errors5) > 0: 
    e5avg = sum(errors5) / len(errors5)
  else:
    e5avg = 0
  if sum(errors10) > 0:
    e10avg = sum(errors10) / len(errors10)
  else:
    e10avg = 0
  if sum(errors15) > 0:
    e15avg = sum(errors15) / len(errors15)
  else:
    e15avg = 0
  if tested: print "We have tried voltage %s already, errors:" %voltage, e5avg, e10avg, e15avg
  if (e5avg+e10avg)/2 > MAX_ERR_RATE:
    print "Voltage setting of %s not recommended, past error avg (5/10/15min avg): %02.f %02.f %02.f" %(voltage, e5avg, e10avg, e15avg)
    return False
  print "Voltage setting of %s good to test." %(voltage, )
  return True


###################
# TUNING FUNCTIONS
###################
def adjust_voltage(freq):
  """decide on what to adjust"""
  for i in range(0,4):
    if str(i+1) in skip_chain:
      print "Chain %i has been excluded by commandline option %s" %(i+1, sys.argv[3])
      continue
    # Voltage needs to go up
    if chain_hist[freq][-1]['error_rate5'][i] > MAX_ERR_RATE:
      if int(chain_hist[freq][-1]['voltage'][i],16) > int(MAX_VOLTAGE,16):
        print "Chain %i needs more voltage (%.2f err/m)" %(i+1, chain_hist[freq][-1]['error_rate5'][i])
        result = inc_voltage(freq, i)
        print "Overvolted chain %i from %s to %s" %(i+1, result[0], result[1])
      else:
        print "Skipped chain %i, max overvolt reached, tune manually if you dare!!" %(i+1,)
    # Voltage can be tuned down more
    elif chain_hist[freq][-1]['error_rate10'][i] == 0 and chain_hist[freq][-1]['error_rate15'][i] < MAX_ERR_RATE * 0.75 \
        and (int(now) - chain_hist[current_stats['frequency']][0]['timestamp']) > 600:
      if int(chain_hist[freq][-1]['voltage'][i],16) < 254:
        print "Chain %i can be undervolted more (%.2f err/m)" %(i+1, chain_hist[freq][-1]['error_rate10'][i])
        result = dec_voltage(freq, i)
        print "Undervolted chain %i from %s to %s" %(i+1, result[0], result[1])
      else:
        print "Skipped chain %i, max undervolt reached." %(i+1,)
  

def dec_voltage(freq, chain):
  """decrease voltage on chain"""
  global current_voltage, last_change
  voltage_step =  min( int(0.35 / ( chain_hist[freq][-1]['error_rate15'][chain] + 0.01 ) ), 7)
  new_voltage = int(current_voltage[chain], 16) + voltage_step
  new_voltage = min(new_voltage, 254)
  while not voltage_history(freq, chain, hex(new_voltage)) and new_voltage > int(current_voltage[chain], 16):
    print "DEBUG:", voltage_history(freq, chain, hex(new_voltage)), new_voltage, int(current_voltage[chain], 16)
    new_voltage = new_voltage - 1
  #print "Voltage history for this voltage/freq/chain:", voltage_history(freq, chain, hex(new_voltage))
  #print "Current/new voltage on chain %i: %s / %s" %(chain+1, chain_hist[freq][-1]['voltage'][chain], hex(new_voltage))
  if int(current_voltage[chain], 16) < 254 and current_voltage != new_voltage:
    voltage_result = set_voltage(miner_ip, chain+1, hex(new_voltage))
    last_change = int(time.time())
  else:
    print "Aborted further decrease of voltage, chain %i is already at %s" %(chain+1, chain_hist[freq][-1]['voltage'][chain])
    #new_voltage = int(chain_hist[freq][-1]['voltage'][chain],16)
    voltage_result = [chain_hist[freq][-1]['voltage'][chain], chain_hist[freq][-1]['voltage'][chain]]
  current_voltage[chain] = hex(new_voltage)
  return voltage_result
  

def inc_voltage(freq, chain):
  """increase voltage on chain"""
  global current_voltage, last_change
  voltage_step = max( int( chain_hist[freq][-1]['error_rate5'][chain] * (TUNE_REPEAT / 60) ), 2 )
  # limit to voltage_step 7
  voltage_step = min(voltage_step, 7)
  new_voltage = int( current_voltage[chain], 16 ) - voltage_step
  new_voltage = max( new_voltage, int(MAX_VOLTAGE,16) )
  while not voltage_history(freq, chain, hex(new_voltage)) and new_voltage < int(current_voltage[chain], 16):
    print "DEBUG:", voltage_history(freq, chain, hex(new_voltage)), new_voltage, int(current_voltage[chain], 16)
    new_voltage = new_voltage + 1
  #print "Voltage history for this voltage/freq/chain:", voltage_history(freq, chain, hex(new_voltage))  
  #print "Current/new voltage on chain %i: %s / %s" %(chain+1, chain_hist[freq][-1]['voltage'][chain], hex(new_voltage))
  if int(current_voltage[chain],16) == int(MAX_VOLTAGE,16) and current_voltage != new_voltage:
    print "Aborted further increase of voltage, chain %i is already at %s" %(chain+1, chain_hist[freq][-1]['voltage'][chain])
    #new_voltage = int(chain_hist[freq][-1]['voltage'][chain], 16)
    voltage_result = [chain_hist[freq][-1]['voltage'][chain], chain_hist[freq][-1]['voltage'][chain]]
  elif new_voltage >= int(MAX_VOLTAGE,16):
    voltage_result = set_voltage(miner_ip, chain+1, hex(new_voltage))
    last_change = int(time.time())
  else: 
    print "Aborted further increase of voltage, chain %i is already at %s" %(chain+1, chain_hist[freq][-1]['voltage'][chain])
    #new_voltage = int(chain_hist[freq][-1]['voltage'][chain], 16)
    voltage_result = [chain_hist[freq][-1]['voltage'][chain], chain_hist[freq][-1]['voltage'][chain]]
  current_voltage[chain] = hex(new_voltage)
  return voltage_result


def check_minerstatus(freq):
  """check for errors on chain or overtemp"""
  for i in range(0,4):
    #print chain_hist[freq][-1]['asic_status'][i]
    if chain_hist[freq][-1]['asic_status'][i].find('x') > -1:
      print "Chain %i has disconnected chips, please check:\n %s" %(i+1, chain_hist[freq][-1]['asic_status'][i])


def sig_handler(signum, frm):
  report_stats()
  print "\nSignal %i caught" %signum
  sys.exit(2)      


def report_stats():
  """report final stats"""
  rep = ""
  rep += "Stats report:\n"
  rep += "*************\n"
  for f in chain_hist.keys():
    sd = datetime.fromtimestamp(chain_hist[f][0]['timestamp'])
    ed = datetime.fromtimestamp(chain_hist[f][-1]['timestamp'])
    rep += "Freq: %s:\n" %f
    rep += "=========\n"
    for stats in chain_hist[f]:
      d = datetime.fromtimestamp(stats['timestamp'])
      rep += "| %2i:%02i.%02i   | %s | %s | %s | %s |\n" \
        %(d.hour, d.minute, d.second, stats['voltage'][0], stats['voltage'][1], stats['voltage'][2], stats['voltage'][3])
      rep += "| Temp Chips | %i C | %i C | %i C | %i C |\n" \
        %(stats['temp_chip'][0], stats['temp_chip'][1], stats['temp_chip'][2], stats['temp_chip'][3])
      rep += "| Err 5min   | %.2f | %.2f | %.2f | %.2f |\n" \
        %(stats['error_rate5'][0], stats['error_rate5'][1], stats['error_rate5'][2], stats['error_rate5'][3])
      rep += "| Err 10min  | %.2f | %.2f | %.2f | %.2f |\n" \
        %(stats['error_rate10'][0], stats['error_rate10'][1], stats['error_rate10'][2], stats['error_rate10'][3])
      rep += "| Err 15min  | %.2f | %.2f | %.2f | %.2f |\n" \
        %(stats['error_rate15'][0], stats['error_rate15'][1], stats['error_rate15'][2], stats['error_rate15'][3])
      rep += "| Err All    | %.2f | %.2f | %.2f | %.2f |\n" \
        %(stats['error_rate'][0], stats['error_rate'][1], stats['error_rate'][2], stats['error_rate'][3])
    rep += "*"*50 + "\n"
    rep += "| Start %2i:%02i.%02i | %s | %s | %s | %s |\n" \
      %(sd.hour, sd.minute, sd.second, chain_hist[f][0]['voltage'][0], chain_hist[f][0]['voltage'][1], chain_hist[f][0]['voltage'][2], chain_hist[f][0]['voltage'][3])
    rep += "| Start %2i:%02i.%02i | %i C | %i C | %i C | %i C |\n" \
      %(sd.hour, sd.minute, sd.second, chain_hist[f][0]['temp_chip'][0], chain_hist[f][0]['temp_chip'][1], chain_hist[f][0]['temp_chip'][2], chain_hist[f][0]['temp_chip'][3])
    rep += "| End   %2i:%02i.%02i | %s | %s | %s | %s |\n" \
      %(ed.hour, ed.minute, ed.second, chain_hist[f][-1]['voltage'][0], chain_hist[f][-1]['voltage'][1], chain_hist[f][-1]['voltage'][2], chain_hist[f][-1]['voltage'][3])
    rep += "| End   %2i:%02i.%02i | %i C | %i C | %i C | %i C |\n" \
      %(ed.hour, ed.minute, ed.second, chain_hist[f][-1]['temp_chip'][0], chain_hist[f][-1]['temp_chip'][1], chain_hist[f][-1]['temp_chip'][2], chain_hist[f][-1]['temp_chip'][3])
    
  print rep
  print "*"*50
  fd,fname = tempfile.mkstemp(suffix='.rep', prefix="%s-" %miner_ip)
  fobj = open(fname, 'w')
  fobj.write(rep)
  fobj.close()
  os.close(fd)
  print "Report written to %s" %fname
  try:
    nobegging
  except:
    shameless_begging()


def show_usage():
  print "Usage:"
  print "%s -i <ip>|--minerip=<ip> [OPTIONS]" %__file__
  print "\nOptions:"
  print " -p <adminpass>\t\t\tadmin password if not set to 'admin'"
  print " --password=<adminpass>"
  print " -s <chain1[,chain2]>\t\tskip one or more chains"
  print " --skip <chain1[,chain2]>" 
  print " --nobegging\t\t\tSuppress the begging message"
  print ""
  print "Examples:"
  print "Tune miner on 10.10.10.33, use '1234' as admin password and skip tuning chain 2 and 3:"
  print "%s -i 10.10.10.33 -p 1234 --skip 2,3" %__file__
  print "Default usage :"
  print "%s -i 10.10.10.33" %__file__
  print ""


def shameless_begging():
  print "\nIf you found this script helpful in tuning your miners, please donate to one of the following addresses:"
  print "BTC: 14kfN1siTWYsLxteeDxXJBfk8ZP74u2Pbz"
  print "LTC: LQiVP7PjUHdaYWmC5JzuPbKKETSc5brisr"
  print "This script uses an improved version of jstefanops set_voltage tool, donate him too while you feel generous enough:"
  print "https://github.com/jstefanop/bitmain-tools"


##################
# MAIN
##################
if __name__ == '__main__':
  if len(sys.argv) < 2:
    print "Incomplete parameters."
    show_usage()
    sys.exit(1)  

  try:                                
    opts, args = getopt.getopt(sys.argv[1:], "hi:p:s:", ["help", "minerip=", "password=", "skip=", "nobegging"])
  except getopt.GetoptError:
    print "Error getting cmdline params."
    show_usage()
    sys.exit(1)
  for opt, arg in opts:
    if opt in ("-h", "--help"):
      show_usage()   
      sys.exit(1)
    if opt in ("-i", "--minerip"):
      miner_ip = arg
    elif opt in ("-p", "--password"):
      admin_pw = arg
    elif opt in ("-s", "--skip"):
      skip_chain = arg.split(",")
      print "Skipping these chains:", skip_chain
    elif opt in ("--nobegging"):
      nobegging = True

  try:
    socket.gethostbyname(miner_ip)
  except:
    print "No or invalid miner ip given, aborting"
    show_usage()
    sys.exit(1)
  try:
    admin_pw = admin_pw 
  except:
    admin_pw = 'admin'
  try:
    skip_chain = skip_chain
  except:
    skip_chain = []

  # catch signals
  signal.signal(signal.SIGINT, sig_handler)
  signal.signal(signal.SIGTERM, sig_handler)

  install_flag = False
  chain_hist = {}
  current_voltage = get_voltage(miner_ip)
  last_vset = {int(time.time()): current_voltage}
  last_change = int(time.time())
  cycle_count = 0
  
  
  # main tuning loop    
  while True:
    #print "last_vset:", last_vset
    now = time.time()
    # get error stats
    current_stats = get_minerstats(miner_ip, port=4028)
    # get current voltage levels
    current_voltage = get_voltage(miner_ip)
    # add to history
    add_history(current_stats, current_voltage, int(now))
    # process history and calculate error/min for 5,10,15 and all
    process_history(current_stats['frequency'])
    # check miner status for errors
    check_minerstatus(current_stats['frequency'])
    # see if we have to adjust voltage every 5min only
    if int(now) - last_vset.keys()[-1] > TUNE_REPEAT -5:
      adjust_voltage(current_stats['frequency'])    
      last_vset = {int(time.time()): current_voltage}
    # limit length of history
    if len(chain_hist[current_stats['frequency']]) > HIST_MAX_LEN:
      chain_hist[current_stats['frequency']].pop(0)
    time_running = (int(time.time()) - chain_hist[current_stats['frequency']][0]['timestamp'])
    print "= Running since: %02i:%02i.%02i, now sleeping for %.1fs =" \
      %(divmod(time_running,60*60)[0], divmod( divmod(time_running, 60*60)[1], 60 )[0], divmod( divmod(time_running, 60*60)[1], 60 )[1], REPEAT - (time.time()-now))
      
    # if we are stable, exit
    if now - last_change > 900:
      report_stats()
      print "Finished tuning, miner stable AFAICS"
      break
    if cycle_count > MAX_CYCLE:
      report_stats()
      print "Reached maximum cycle limit of %i without getting stable enough results, aborting tuning." %cycle_count
      break
    else:
      cycle_count += 1
    # sleep a while..
    sleep_time = REPEAT - (time.time()-now) 
    if sleep_time < 0:
      time.sleep(5)
    else:
      time.sleep(sleep_time)
      




# chain_hist format:
"""
{u'400': [
  {'chainrate': [u'131.20', u'130.93', u'130.97', u'130.80'], 
  'error_rate': [0.0, 0.0, 0.0, 0.0],  
  'error_rate5': [0.0, 0.0, 0.0, 0.0],  
  'error_rate10': [0.0, 0.0, 0.0, 0.0],
  'error_rate15': [0.0, 0.0, 0.0, 0.0],
  'uptime': 282139, 
  'err': [3, 10167, 186, 2], 
  'speed': [520.37, u'523.897'], 
  'asic_status': [u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo', u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo', u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo', u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo'], 
  'temp_chip': [61, 63, 63, 60], 
  'frequency': u'400', 
  'voltage': ['0xe5', '0xa4', '0xa5', '0xc0'], 
  'timestamp': 1526135868, 
  'device_error': 0.0, 
  'temp_pcb': [55, 56, 56, 54]}
  ,  
  {'chainrate': [u'130.81', u'130.34', u'131.27', u'130.34'], 
  'error_rate': [0.0, 2.0, 0.0, 0.0],  
  'error_rate5': [0.0, 2.0, 0.0, 0.0],  
  'error_rate10': [0.0, 2.0, 0.0, 0.0],
  'error_rate15': [0.0, 2.0, 0.0, 0.0],
  'uptime': 282169, 
  'err': [3, 10168, 186, 2], 
  'speed': [520.37, u'522.769'], 
  'asic_status': [u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo', u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo', u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo', u' oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo oooooooo'], 
  'temp_chip': [61, 63, 63, 60], 
  'frequency': u'400', 
  'voltage': ['0xe5', '0xa4', '0xa5', '0xc0'], 
  'timestamp': 1526135898, 
  'device_error': 0.0, 
  'temp_pcb': [55, 56, 56, 54]}
  ]
}
"""
