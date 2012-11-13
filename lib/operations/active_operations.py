#!/usr/bin/env python

#/*******************************************************************************
# Copyright (c) 2012 IBM Corp.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#/*******************************************************************************

'''
    Created on Nov 27, 2011

    Active Object Operations Library

    @author: Marcio A. Silva, Michael R. Hines
'''
from os import chmod, access, F_OK
from random import choice, randint
from time import time, sleep
from subprocess import Popen, PIPE
from re import sub
from uuid import uuid5, NAMESPACE_DNS

from lib.remote.ssh_ops import repeated_ssh
from lib.remote.process_management import ProcessManagement
from lib.auxiliary.code_instrumentation import trace, cbdebug, cberr, cbwarn, cbinfo, cbcrit
from lib.auxiliary.data_ops import str2dic, dic2str, wait_on_process, DataOpsException
from lib.auxiliary.value_generation import ValueGeneration
from lib.stores.stores_initial_setup import StoreSetupException
from lib.auxiliary.thread_pool import ThreadPool
from lib.auxiliary.data_ops import selective_dict_update
from lib.auxiliary.config import parse_cld_defs_file, load_store_functions, get_startup_commands
from lib.clouds.shared_functions import CldOpsException
from base_operations import BaseObjectOperations

import copy
import threading

class ActiveObjectOperations(BaseObjectOperations) :
    '''
    TBD
    '''

    @trace
    def cldattach(self, cld_attr_lst, params, definitions, cmd, \
                  uni_attr_lst = None) :
        '''
        TBD
        '''
        try :
            _fmsg = "unknown error: "
            _cld_name = None
            _status, _msg = self.parse_cli(cld_attr_lst, params, cmd)

            if _status :
                return self.package(_status, _msg, None)

            self.conn_check("OS")
            _attached_cloud_list = self.osci.get_object_list("CLOUD")

            if _attached_cloud_list :
                _attached_cloud_list = list(_attached_cloud_list)
            else :
                _attached_cloud_list = []

            if cld_attr_lst["cloud_name"] in _attached_cloud_list :

                self.cn = cld_attr_lst["cloud_name"]
                self.oscp["cloud_name"] = self.cn
                self.osci = False

                self.conn_check("OS")
                cld_attr_lst = self.get_cloud_parameters(cld_attr_lst["cloud_name"])
                
                _idmsg = "The \"" + cld_attr_lst["model"] + "\" cloud named \""
                _idmsg += cld_attr_lst["name"] + "\""
                _smsg = _idmsg + " was already attached to this experiment."
                _fmsg = _idmsg + " could not be attached to this experiment: "
                
                _time_attr_list = self.osci.get_object("GLOBAL", False, "time", False)
                _expid = _time_attr_list["experiment_id"]
                _cld_name = cld_attr_lst["name"]
            else :
                '''
                 Three possibilities:
                 1. We parse a new cloud_definitions.txt from the CLI
                 2. We parse a new cloud_definitions from the API
                 3. We end-up re-parsing the default cloud_definitions.txt
                    - This one is equally important if you have multiple
                      clouds defined in a single definitions file
                      because all of the cloud templates are included by
                      default now - resulting in the unused ones being
                      thrown away below. So, it's necessary to re-parse
                      the file automatically and re-load them if a cloud
                      with a different model is attached in a single
                      configuration file.
                '''
                if cld_attr_lst["cloud_filename"] and not definitions :
                    fh = open(self.path + "/" + cld_attr_lst["cloud_filename"], 'r')
                    definitions = fh.read()
                    fh.close()
                    
                cld_attr_lst.update(parse_cld_defs_file(self.pid, definitions))
                '''
                First, we have new support in the GUI for single-file configurations
                that are capable of hosting multiple cloud configurations in a single file.
                
                This is done through the use of the "CLOUDOPTION_XXX" user-defined keyword.
                
                For this to work, we need to search through all the keys and re-write the
                keynames so that they look the way they are supposed to before we try
                to attach the cloud. 
                '''
                # First, Make a pass through config.py to verify that any available
                # CONFIGOPTION keywords are properly installed
                available_clouds = get_startup_commands(cld_attr_lst, return_all_options = True)
                if len(available_clouds) :
                    for cloud_name in available_clouds :
                        if cld_attr_lst["cloud_name"].lower() != cloud_name :
                            continue
                        searchkey = "cloudoption_" + cloud_name
                        for _category in cld_attr_lst.keys() :
                            if not isinstance(cld_attr_lst[_category], dict) :
                                continue 
                            for  _attribute in cld_attr_lst[_category].keys() :
                                if _attribute.count(searchkey) :
                                    # Don't rewrite the cloudoption keyword
                                    # indicators themselves
                                    if _category == "user-defined" and _attribute.lower() == searchkey :
                                        continue
                                    _new = _attribute.replace(searchkey + "_", "")
                                    cld_attr_lst[_category][_new] = cld_attr_lst[_category][_attribute]
                                    # Remove the unneeded ones
                                    del cld_attr_lst[_category][_attribute]
                        break

                '''
                The ports for the Object Store, Log Store and Metric Store were
                already determined by the init method on CBCLI, since those are
                now part of the "universal" (i.e., "above the clouds") config. 
                These values, present on the universal configuration file, need 
                to be rewritten here.
                '''
                if uni_attr_lst :
                    cld_attr_lst["objectstore"].update(uni_attr_lst["objectstore"])
                    cld_attr_lst["logstore"].update(uni_attr_lst["logstore"])
                    cld_attr_lst["metricstore"].update(uni_attr_lst["metricstore"])
                
                '''
                  At this point in the attachment, the configuration dictionary
                  has the configurations of all possible clouds in the form of:
                   
                    cld_attr_lst[*][model + "_cloudconfig_" + attribute]
                 
                  Now that we know the model the user actually cares about, we need
                  to re-write the configuration so the rest of the operations code
                  functions the same way it did before.
                '''
                for _category in cld_attr_lst.keys() :
                    if isinstance(cld_attr_lst[_category], dict) :
                        for  _attribute in cld_attr_lst[_category].keys() :
                            if _attribute.count("_cloudconfig_") :
                                if _attribute.count(cld_attr_lst["model"] + "_cloudconfig_") :
                                    _new = _attribute.replace(cld_attr_lst["model"] + "_cloudconfig_", "")
                                    cld_attr_lst[_category][_new] = cld_attr_lst[_category][_attribute]
                                # Remove the unneeded ones
                                del cld_attr_lst[_category][_attribute]
                                
                '''
                  Next, we need to check the current cloud model's configuration
                  for any variables that the User forgot to perform by searching for
                  a specific "need_to_be_configured_by_user" keyword
                '''
                for _category in cld_attr_lst :
                    if isinstance(cld_attr_lst[_category], dict) and _category != "user-defined" :
                        for  _attribute in cld_attr_lst[_category] :
                            if cld_attr_lst[_category][_attribute] == "need_to_be_configured_by_user" :
                                # Fixup custom multi-cloud options that 
                                # are pulled in from the templates
                                template_key = cld_attr_lst["model"] + "_" + _attribute
                                if template_key in cld_attr_lst["user-defined"] :
                                    cld_attr_lst[_category][_attribute] = cld_attr_lst["user-defined"][template_key]
                                    continue
                                _msg = "Your configuration file is missing the following configuration: \n"
                                _msg += "\t[USER-DEFINED]\n"
                                if "startup_cloud" in cld_attr_lst["user-defined"] :
                                    _msg += "\tCLOUDOPTION_" + cld_attr_lst["name"] + "_" + cld_attr_lst["model"].upper() + "_" + _attribute.upper() + " = XXXXX\n"
                                else :
                                    _msg += "\t" + cld_attr_lst["model"].upper() + "_" + _attribute.upper() + " = XXXXX\n"
                                _msg += "\n"
                                _msg += "Please update your configuration and try again."
                                raise Exception(_msg)
                            
    
                _idmsg = "The \"" + cld_attr_lst["model"] + "\" cloud named \""
                _idmsg += cld_attr_lst["cloud_name"] + "\""
                _smsg = _idmsg + " was successfully attached to this "
                _smsg += "experiment."
                _fmsg = _idmsg + " could not be attached to this experiment: "
        
                _cld_ops = __import__("lib.clouds." + cld_attr_lst["model"] \
                                      + "_cloud_ops", fromlist = \
                                      [cld_attr_lst["model"].capitalize() + "Cmds"])
    
                _cld_ops_class = getattr(_cld_ops, \
                                         cld_attr_lst["model"].capitalize() + "Cmds")
    
                # User may have an empty VMC list. Need to be able to handle that.
                if cld_attr_lst["vmc_defaults"]["initial_vmcs"].strip() != "" :
                    _tmp_vmcs = cld_attr_lst["vmc_defaults"]["initial_vmcs"].split(",")
                    _vmcs = ""
                    for vmc in _tmp_vmcs :
                        _vmcs += vmc
                        if not vmc.count(":") :
                            _vmcs += ":sut"
                        _vmcs += ","
                            
                    _vmcs = _vmcs[:-1]
                    _initial_vmcs = str2dic(_vmcs)
                else :
                    _initial_vmcs = []
                    _cld_conn = _cld_ops_class(self.pid, None, None)
    
                _msg = "Attempting to connect to all VMCs described in the cloud "
                _msg += "defaults file, in order to check the access parameters "
                _msg += "and security credentials"
                cbdebug(_msg)
    
                for _vmc_entry in _initial_vmcs :
                    _cld_conn = _cld_ops_class(self.pid, None, None)
                    _cld_conn.test_vmc_connection(_vmc_entry.split(':')[0], \
                                                  cld_attr_lst["vmc_defaults"]["access"], \
                                                  cld_attr_lst["vmc_defaults"]["credentials"], \
                                                  cld_attr_lst["vmc_defaults"]["extra_info"])
    
                cld_attr_lst["instance"] = self.pid + ':' + cld_attr_lst["cloud_name"]
                _all_global_objects = cld_attr_lst.keys()
                cld_attr_lst["client_should_refresh"] = "no"
    
                _remove_from_global_objects = [ "name", "model", "user-defined", \
                                               "dependencies", "cloud_filename", \
                                               "cloud_name", "instance", "objectstore", \
                                               "command_originated", "state", \
                                               "tracking", "channel", "sorting", \
                                               "ai_arrived", "ai_departed", \
                                               "ai_failed", "ai_reservations" ]
    
                for _object in _remove_from_global_objects :
                    if _object in _all_global_objects :
                        _all_global_objects.remove(_object)
    
                cld_attr_lst["all"] = ','.join(_all_global_objects)
                cld_attr_lst["all_vmcs_attached"] = "false"
                cld_attr_lst["description"] = _cld_conn.get_description()
                cld_attr_lst["username"] = cld_attr_lst["time"]["username"]
                cld_attr_lst["start_time"] = str(int(time()))
                cld_attr_lst["client_should_refresh"] = "yes"

                # While setting up the Object Store, check for free ports for the 
                # API, GUI, and Gmetad (Host OS performance data collection)
                # daemons, using their indicated ports as the base port
                _proc_man =  ProcessManagement(username = cld_attr_lst["username"] , cloud_name = cld_attr_lst["cloud_name"])
                cld_attr_lst["mon_defaults"]["collector_multicast_port"] = _proc_man.get_free_port(cld_attr_lst["mon_defaults"]["collector_multicast_port"], protocol = "tcp")
                cld_attr_lst["mon_defaults"]["collector_aggregator_port"] = _proc_man.get_free_port(cld_attr_lst["mon_defaults"]["collector_aggregator_port"], protocol = "tcp")
                cld_attr_lst["mon_defaults"]["collector_summarizer_port"] = _proc_man.get_free_port(cld_attr_lst["mon_defaults"]["collector_summarizer_port"], protocol = "tcp")
    
                os_func, ms_func, unused  = load_store_functions(cld_attr_lst)

                os_func(self.pid, cld_attr_lst["instance"], cld_attr_lst, "initialize")
                ms_func(self.pid, cld_attr_lst["instance"], cld_attr_lst, "initialize")
    
                # This needs to be better coded later. Right now, it is just a fix to avoid
                # the problems caused by the fact that git keeps resetting RSA's private key
                # back to 644 (which are too open).
                chmod(cld_attr_lst["space"]["credentials_dir"] + '/' + cld_attr_lst["space"]["ssh_key_name"], 0600)
    
                _expid = cld_attr_lst["time"]["experiment_id"]
                _cld_name = cld_attr_lst["name"]
    
            _status = 0
    
            _msg = _smsg + "\nThe instance identifier is " + cld_attr_lst["instance"]
            _msg += ". \nThe experiment identifier is "
            _msg += _expid
            cld_attr_lst["cloud_name"] = _cld_name

        except ImportError, msg :
            _status = 8
            _msg = _fmsg + str(msg)

        except OSError, msg :
            _status = 8
            _msg = _fmsg + str(msg)

        except AttributeError, msg :
            _status = 8
            _msg = _fmsg + str(msg)

        except DataOpsException, obj :
            _status = 8
            _msg = _fmsg + str(msg)

        # Chicken and the egg problem:
        # Can't catch this exception if the import fails
        # inside of the try/catch ... find another solution
        #except _cld_ops_class.CldOpsException, obj :
        #    _status = str(obj.msg)
        #    _msg = _fmsg + str(obj.msg)

        except StoreSetupException, obj :
            _status = str(obj.status)
            _msg = _fmsg + str(obj.msg)

        except ProcessManagement.ProcessManagementException, obj :
            _status = str(obj.status)
            _msg = _fmsg + str(obj.msg)

        except Exception, e :
            _status = 23
            _msg = _fmsg + str(e)
        
        finally :
            if _status :
                cberr(_msg)
            else :
                cbdebug(_msg)
    
            return self.package(_status, _msg, cld_attr_lst)

    @trace    
    def clddetach(self, cld_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _update_frequency = 5
            _max_tries = 10
            _obj_type = command.split('-')[0].upper()

            _status, _fmsg = self.parse_cli(cld_attr_list, parameters, command)

            if not _status :

                if self.cn == cld_attr_list["name"] :
                    True
                else :
                    self.cn = cld_attr_list["name"]
                    self.oscp["cloud_name"] = self.cn
                    #self.osci.disconnect()
                    self.osci = False

                self.conn_check()

                _cld_attr_list = self.osci.get_object("CLOUD", False, cld_attr_list["name"], False)

                self.update_cloud_attribute("client_should_refresh", "yes")

                _msg = "Waiting for all active AIDRS daemons to finish gracefully...." 
                cbdebug(_msg, True)

                _curr_tries = 0
                _aidrs_list = self.osci.get_object_list("AIDRS")
                while _aidrs_list :
                    cbdebug("Still waiting.... ")
                    _aidrs_list = self.osci.get_object_list("AIDRS")

                    if _aidrs_list and _curr_tries < _max_tries :
                        for _aidrs_uuid in _aidrs_list :
                            _aidrs_attr_list = self.osci.get_object("AIDRS", \
                                                                 False, \
                                                                 _aidrs_uuid, \
                                                                 False)

                            _x_attr_list = {}
                            _parameters = self.cn + ' ' + _aidrs_attr_list["name"] + " true"
                            self.objdetach(_x_attr_list, _parameters, "aidrs-detach")

                        sleep(_update_frequency)
                        _curr_tries += 1
                    else :
                        break
                
                if _curr_tries >= _max_tries :
                    _msg = "Some AIDRS (daemons) did not die after " 
                    _msg += str(_max_tries * _update_frequency) + " seconds."
                    _msg += "Please kill them manually after the end of the "
                    _msg += "cloud detachment process (a \"pkill -f aidrs-submit\""
                    _msg += " should suffice)."
                else :
                    _msg = "All AIDRS (daemons and objects were removed)." 
                cbdebug(_msg, True)
                sleep (_update_frequency) 

                _curr_tries = 0
                for _object_typ in [ "AI", "VM", "SVM", "VMC"] :            
                    _msg = "Giving extra time for all " + _object_typ + "s to " 
                    _msg += "finish attachment/detachment gracefully......"
                    cbdebug(_msg)
                    
                    _active = int(self.get_object_count(self.cn, _object_typ, "ARRIVING"))
                    _active += int(self.get_object_count(self.cn, _object_typ, "DEPARTING"))

                    while _active :
                        cbdebug(str(_active) + ' ' + _object_typ + "s are still attaching/detaching....")
                    
                    _active = int(self.get_object_count(self.cn, _object_typ, "ARRIVING"))
                    _active += int(self.get_object_count(self.cn, _object_typ, "DEPARTING"))

                    if _active :
                        if _curr_tries < _max_tries :
                            sleep (_update_frequency)
                            _curr_tries += 1
                        else :
                            break
                
                    if _curr_tries >= _max_tries :
                        _msg = "Some " + _obj_type + " attach (daemons) did not die after " 
                        _msg += str(_max_tries * _update_frequency) + " seconds."
                        _msg += "Please kill them manually after the end of the "
                        _msg += "cloud detachment process (a \"pkill -f " + _obj_type.lower() + "-attach\""
                        _msg += " should suffice)."
                    else :
                        _msg = "Done"
                    cbdebug(_msg)

                    _msg = "Removing all " + _object_typ + " objects attached to"
                    _msg += " this experiment."
                    cbdebug(_msg, True)
                    
                    _obj_list = self.osci.get_object_list(_object_typ)
                    if _obj_list :
                        for _obj_uuid in _obj_list :
                            _obj_attr_list = self.osci.get_object(_object_typ, False, _obj_uuid, False)
                            _x_attr_list = {}
                            if BaseObjectOperations.default_cloud is not None :
                                _parameters = _obj_attr_list["name"] + " true"
                            else :
                                _parameters = self.cn + ' ' + _obj_attr_list["name"] + " true"

                            self.objdetach(_x_attr_list, _parameters, _object_typ.lower() + "-detach")

                        cbdebug("Done", True)
                
                sleep (_update_frequency)

                _proc_man = ProcessManagement(username = _cld_attr_list["username"], cloud_name = self.cn)
                _gmetad_pid = _proc_man.get_pid_from_cmdline("gmetad.py")

                if len(_gmetad_pid) :
                    cbdebug("Killing the running Host OS performance monitor (gmetad.py)......", True)
                    _proc_man.kill_process("gmetad.py", self.cn)

                _msg = "Removing all contents from Object Store (GLOBAL objects,"
                _msg += "VIEWS, etc.)"
                cbdebug(_msg, True)
                
                self.osci.clean_object_store(_cld_attr_list)

                _status = 0

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg =  str(obj.msg)

        except ProcessManagement.ProcessManagementException, obj :
            _status = str(obj.status)
            _msg = _fmsg + str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:
            if _status :
                _msg = "Cloud " + cld_attr_list["name"] + " could not be " 
                _msg += "detached from this experiment : " + _fmsg
                cberr(_msg)
            else :
                _msg = "Cloud " + cld_attr_list["name"] + " was successfully " 
                _msg += "detached from this experiment."
                cbdebug(_msg)

            return _status, _msg, False

    def hostfail_repair(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            
            obj_attr_list["name"] = "undefined"
            obj_attr_list["target_state"] = "undefined"
            _host_already_failed = False
            
            _obj_type, _target_state = command.upper().split('-')
            
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status = 101
                
                if self.cn == obj_attr_list["cloud_name"] :
                    True
                else :
                    self.cn = obj_attr_list["cloud_name"]
                    self.oscp["cloud_name"] = self.cn
                    #self.osci.disconnect()
                    self.osci = False
                
                self.conn_check()
                _firs_defaults = self.osci.get_object("GLOBAL", False, "firs_defaults", False)
                _host_list = str2dic(_firs_defaults["hosts"])
                obj_attr_list["switch_ip"] = _firs_defaults["switch_ip"]

                if obj_attr_list["name"] in _host_list :
                    obj_attr_list["port"] = _host_list[obj_attr_list["name"]]
                    _status = 0
                else :
                    _fmsg = "Host \"" + obj_attr_list["name"] + "\" is not "
                    _fmsg += "listed as a target for a failure request."

            if not _status :
                _status = 101
                _failed_hosts = self.osci.get_list(_obj_type, "FAILED_HOSTS", True)

                for _failed_host in _failed_hosts :

                    if _failed_host[0] == obj_attr_list["name"] :
                        if _target_state.lower() == "fail" :
                            _fmsg = "Host \"" + obj_attr_list["name"] + "\" is "
                            _fmsg += "already at the \"failed\" state"
                            _host_already_failed = True
                        elif _target_state.lower() == "repair" :
                            _msg = "Sending the startup command to port \""
                            _msg += obj_attr_list["port"] + "\" on the switch at "
                            _msg += obj_attr_list["switch_ip"] + " in order to "
                            _msg += "repair HOST " + obj_attr_list["name"]
                            cbdebug(_msg)
                            self.osci.remove_from_list("HOST", "FAILED_HOSTS", obj_attr_list["name"], True)
                        break

                if _target_state.lower() == "fail" and not _host_already_failed :
                    _msg = "Sending the shutdown command to port \"" + obj_attr_list["port"]
                    _msg += "\" on the switch at " + obj_attr_list["switch_ip"]
                    _msg += " in order to fail HOST " + obj_attr_list["name"]
                    cbdebug(_msg)
                    self.osci.add_to_list("HOST", "FAILED_HOSTS", obj_attr_list["name"], int(time()))            
    
                _status = 0

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "HOST \"" + obj_attr_list["name"] + "\" could"
                _msg += " not be " + _target_state.lower() + "ed on this "
                _msg += "experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = "HOST \"" + obj_attr_list["name"] + "\" was "
                _msg += "successfully " + _target_state.lower() + "ed on this "
                _msg += "experiment." 
                cbdebug(_msg)
            return _status, _msg, None

    @trace    
    def vmccleanup(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            obj_attr_list["name"] = "undefined"
            _obj_type = command.split('-')[0].upper()
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

                if not _status :                    
                    _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])            
                    _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])
    
                    _status, _fmsg = _cld_conn.vmccleanup(obj_attr_list)


        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "VMC object named \"" + obj_attr_list["name"] + "\" could"
                _msg += " not be cleaned up on this experiment: "
                _msg += _fmsg
                cberr(_msg)
            else :
                _msg = "VMC object named \"" + obj_attr_list["name"] + "\" was "
                _msg += "sucessfully cleaned up on this experiment." 
                cbdebug(_msg)
            return _status, _msg, None

    @trace    
    def vmcattachall(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _smsg = ''
            
            obj_attr_list["name"] = "undefined"
            
            _obj_type = command.split('-')[0].upper()
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

            _vmc_defaults = self.osci.get_object("GLOBAL", False, \
                                                 "vmc_defaults", False)

            _cloud_parameters = self.get_cloud_parameters(self.cn)

            if _cloud_parameters["all_vmcs_attached"].lower() == "false" :
                _obj_type = command.split('-')[0].upper()
                _obj_attr_list = {}

                if BaseObjectOperations.default_cloud is not None:
                    _obj_attr_list["cloud_name"] = BaseObjectOperations.default_cloud
                else :
                    _obj_attr_list["cloud_name"] = parameters.split()[0]

                _obj_attr_list["command_originated"] = int(time())
                _obj_attr_list["command"] = "vmcattach " + self.cn + " all"
                _obj_attr_list["name"] = "all"

                self.get_counters(_obj_attr_list["cloud_name"], _obj_attr_list)
                self.record_management_metrics(_obj_attr_list["cloud_name"], "VMC", _obj_attr_list, "trace")

                _obj_attr_list["parallel_operations"] = {}
                _vmc_counter = 0
                if _vmc_defaults["initial_vmcs"].strip() == "" :
                    _fmsg = "Your configuration template files are probably included in the wrong order."
                    _status = 12
                    raise self.ObjectOperationException(_fmsg, 12)

                else :
                    for _vmc_element in _vmc_defaults["initial_vmcs"].split(',') :
                        _vmc_vars = _vmc_element.split(":")
                        _vmc_cloud_name = _vmc_vars[0]
                        if len(_vmc_vars) == 2 :
                            _pools = _vmc_vars[1]
                        else :
                            _pools = "sut"
                        _obj_attr_list["parallel_operations"][_vmc_counter] = {}
                        _obj_attr_list["parallel_operations"][_vmc_counter]["uuid"] = str(uuid5(NAMESPACE_DNS, str(randint(0,10000000000000000)))).upper()
                        _obj_attr_list["parallel_operations"][_vmc_counter]["parameters"] = _obj_attr_list["cloud_name"]  + ' ' + _vmc_cloud_name + ' ' + _pools
                        _obj_attr_list["parallel_operations"][_vmc_counter]["operation"] = "vmc-attach"
                        _vmc_counter += 1

                    _obj_attr_list["attach_parallelism"] = _vmc_counter            
                    _status, _fmsg = self.parallel_obj_operation("attach", _obj_attr_list)

                    if not _status :
                        self.update_cloud_attribute("all_vmcs_attached", "true")

                        self.update_host_os_perfmon()

                        _status = 0
            else :
                _smsg = " It looks like all VMCs were already attached." 
                #_smsg = " There is "
                #_smsg += "no need to re-attach them. Check it with the "
                #_smsg += "\"vmclist " + self.cn + "\" command. If that is in "
                #_smsg += "error, please issue the command \" cldalter vmc_defaults"
                #_smsg += " all_vmcs_attached=false " + self.cn + "\" and try again"
                _status = 0

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "Failure while attaching all VMCs to this "
                _msg += "experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = "All VMCs successfully attached to this experiment." + _smsg
                cbdebug(_msg)

            return self.package(_status, _msg, None)

    @trace
    def pre_attach_vmc(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100   
            _fmsg = "An error has occurred, but no error message was captured"
            _conf_vmc_list = {}

            _monitor_attr_list = self.osci.get_object("GLOBAL", False, \
                                                      "mon_defaults", False)

            obj_attr_list["collect_from_host"] = _monitor_attr_list["collect_from_host"]
            
            if "initial_vmcs" in obj_attr_list and obj_attr_list["initial_vmcs"].strip() != "":
                for _vmc_element in obj_attr_list["initial_vmcs"].split(',') :
                    _vmc_vars = _vmc_element.split(":")
                    _vmc_cloudname = _vmc_vars[0]
                    if len(_vmc_vars) == 2 :
                        _pools = _vmc_vars[1]
                    else :
                        _pools = "sut"
                    _conf_vmc_list[_vmc_cloudname] = sub(r';', ',', _pools)
    
                if obj_attr_list["name"] in _conf_vmc_list :
                    obj_attr_list["pool"] = _conf_vmc_list[obj_attr_list["name"]]
                else :
                    obj_attr_list["pool"] = "sut"

                del obj_attr_list["initial_vmcs"]

            else :
                obj_attr_list["pool"] = "none"
                
            obj_attr_list["nr_vms"] = 0
                
            self.initialize_metric_name_list()

            _status = 0

        except IndexError, msg :
            _status = 40
            _fmsg = str(msg)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VMC pre-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMC pre-attachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_attach_vm(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100

            _pool_selected = False
            _fmsg = "An error has occurred, but no error message was captured"

            obj_attr_list["vmc_pool"] = obj_attr_list["pool"]
            del obj_attr_list["pool"]

            _vmc_pools = self.osci.get_list("GLOBAL", "vmc_pools")

            _vmc_pools = list(_vmc_pools)

            if obj_attr_list["vmc_pool"] != "auto" :
                
                if not obj_attr_list["vmc_pool"].upper() in _vmc_pools :
                    obj_attr_list["vmc_pool"] = "auto"
                else :
                    obj_attr_list["vmc_pool"] = obj_attr_list["vmc_pool"].upper()
                    _pool_selected = True

            if obj_attr_list["vmc_pool"] == "auto" :

                if "vmc_pool_blacklist" in obj_attr_list.keys() :
                    _blacklist = obj_attr_list["vmc_pool_blacklist"].split(",")
                    for _bad_pool in _blacklist :
                        for _idx in range(0, len(_vmc_pools)) :
                            if _bad_pool.upper() == _vmc_pools[_idx] :
                                del _vmc_pools[_idx]
                                break

                if not len(_vmc_pools) :
                    _status = 181
                    _fmsg = "No additional VMC pools available for VM creation"
                    raise self.oscc.ObjectStoreMgdConnException(_fmsg, _status)

                for _key in obj_attr_list.keys() :
                    if _key.count("pref_pool") :
                        _pref_pool_role = _key.split("_pref_pool")[0]
                        _pref_pool_name = obj_attr_list[_key].upper()
                        if obj_attr_list["role"].count(_pref_pool_role) and _pref_pool_name in _vmc_pools :
                            obj_attr_list["vmc_pool"] = _pref_pool_name
                            _pool_selected = True
                            break
                        
                if not _pool_selected :
                    if "SUT" in _vmc_pools :
                        obj_attr_list["vmc_pool"] = "SUT"
                    else :
                        obj_attr_list["vmc_pool"] = choice(_vmc_pools)

            _vm_uuid_list = self.osci.query_by_view("VMC", \
                                                    "BYPOOL", \
                                                    obj_attr_list["vmc_pool"])

            if len(_vm_uuid_list) :
                obj_attr_list["vmc"] = choice(_vm_uuid_list).split('|')[0]
                
                if not obj_attr_list["vmc"] :
                    _status = 181
                    _msg = "No VMCs on pool \"" +  obj_attr_list["vmc_pool"] + "\""
                    _msg += " are available for VM creation."
                    raise self.oscc.ObjectStoreMgdConnException(_msg, _status)
    
                _vmc_attr_list = self.osci.get_object("VMC", False, \
                                                      obj_attr_list["vmc"], False)
    
                obj_attr_list["vmc_max_vm_reservations"] = _vmc_attr_list["max_vm_reservations"]
                obj_attr_list["vmc_name"] = _vmc_attr_list["name"]
                obj_attr_list["vmc_cloud_ip"] = _vmc_attr_list["cloud_ip"]
                if "svm_destination" in _vmc_attr_list and "svm_stub_ip" in obj_attr_list :
                    obj_attr_list["svm_stub_ip"] = _vmc_attr_list["svm_destination"]
                obj_attr_list["vmc_access"] = _vmc_attr_list["access"]
    
                _vm_templates = self.osci.get_object("GLOBAL", False, \
                                                     "vm_templates", False)

                _vm_template_attr_list = str2dic(_vm_templates[obj_attr_list["role"]])
                
                if obj_attr_list["size"] == "load_balanced_default" :
                    if "lb_size" in _vm_template_attr_list :
                        obj_attr_list["size"] = _vm_template_attr_list["lb_size"]
                    else :
                        obj_attr_list["size"] = "default"
                    
                selective_dict_update(self.pid, obj_attr_list, _vm_template_attr_list)

                _status = 0

            else :
                self.osci.remove_from_list("GLOBAL", "vmc_pools", obj_attr_list["vmc_pool"])
                _fmsg = "An empty VMC pool was selected. This pool was already "
                _fmsg += "removed from the list of pools."
                cbdebug(_fmsg)
                _status = 1819
                
            if not _status :
                _status, _fmsg = self.auto_allocate_vm_port("qemu_debug", obj_attr_list)
                
        except KeyError, msg :
            _status = 40
            _fmsg = "Unknown VM role: " + str(msg)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = 40
            _fmsg = str(obj.msg)

        except DataOpsException, obj :
            _status = 40
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VM pre-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VM pre-attachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_attach_svm(self, obj_attr_list) : 
        '''
        TBD
        '''

        #Don't want exceptions to be caught here. Let them propagate to
        #upper-level handling code....
        
        obj_attr_list["vmc_pool_blacklist"] = obj_attr_list["primary_vmc_pool"]
        obj_attr_list["svm_stub_ip"] = "undefined" 
        self.pre_attach_vm(obj_attr_list)
        
        #Now start the exceptions...
        
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _status, _fmsg = self.auto_allocate_vm_port("replication", obj_attr_list)
            if not _status :
                _status, _fmsg = self.auto_allocate_vm_port("svm_qemu_debug", obj_attr_list)
                
            if not _status :
                _status, _fmsg = self.auto_allocate_vm_port("rdma", obj_attr_list)
                
        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = 40
            _fmsg = str(obj.msg)

        except DataOpsException, obj :
            _status = 40
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "SVM pre-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "SVM pre-attachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_attach_ai(self, obj_attr_list) :
        '''
        TBD
        '''

        #Don't want exceptions to be caught here. Let them propagate to
        #upper-level handling code....
        
        if "save_on_attach" in obj_attr_list and obj_attr_list["save_on_attach"].lower() == "true" :
            cbdebug("Warning: this VM will be saved after attachment. " + \
                    "If this is not what you want, then CTRL-C and use cldalter command.", True)

        #Now start the exceptions...
            
        try :
            _status = 100
            _post_speculative_admission_control = False
            _fmsg = "An error has occurred, but no error message was captured"

            _vmc_list = self.osci.get_object_list("VMC")
            if not _vmc_list :
                _msg = "No VMC attached to this experiment. Please "
                _msg += "attach at least one VMC, or the VM creations triggered "
                _msg += "by this AI attachment operation will fail."
                raise self.oscc.ObjectStoreMgdConnException(_msg, _status)

            if "aidrs" in obj_attr_list and obj_attr_list["aidrs"].lower() != "none" :

                _object_exists = self.osci.object_exists("AIDRS", obj_attr_list["aidrs"], False)

                if _object_exists :
                    _aidrs_attr_list = self.osci.get_object("AIDRS", False, \
                                                          obj_attr_list["aidrs"], False)
        
                    obj_attr_list["max_ais"] = _aidrs_attr_list["max_ais"]
                    obj_attr_list["aidrs_name"] = _aidrs_attr_list["name"]
                    obj_attr_list["pattern"] = _aidrs_attr_list["pattern"]

                else :
                    obj_attr_list["max_ais"] = 1
                    obj_attr_list["aidrs_name"] = "orphan (" + obj_attr_list["aidrs"] + ")"
                    obj_attr_list["pattern"] = "unknown"
            else :
                    obj_attr_list["max_ais"] = 1
                    obj_attr_list["aidrs_name"] = "none"
                    obj_attr_list["pattern"] = "none"
                                        
            _ai_templates = self.osci.get_object("GLOBAL", False, \
                                                 "ai_templates", False)
            _ai_template_attr_list = {}
            _app_type = obj_attr_list["type"]
            for _attrib, _val in _ai_templates.iteritems() :
                if _attrib.count(_app_type) :
                    _ai_template_attr_list[_attrib] = _val

            if not len(_ai_template_attr_list) :
                _status = 40
                _msg = "Unknown AI type: " + _app_type
                raise self.ObjectOperationException(_msg, _status)
            
            # This is just a trick to remove the application name from the
            # start of the AI attributes on the template. 
            # For instance, instead of adding the key  "hadoop_driver_hadoop_setup1"
            # to the list of attributes of the AI we want the key to be in fact 
            # only "hadoop_driver_hadoop_setup1"
            _x = len(_app_type) + 1

            for _key, _value in _ai_template_attr_list.iteritems() :
                if _key.count(_app_type) :
                    if _key[_x:] in obj_attr_list : 
                        if obj_attr_list[_key[_x:]] == "default" :
                            obj_attr_list[_key[_x:]] = _value
                    else :
                        obj_attr_list[_key[_x:]] = _value

            if "lifetime" in obj_attr_list and obj_attr_list["lifetime"] != "none" :
                _value_generation = ValueGeneration(self.pid)
                obj_attr_list["lifetime"] = int(_value_generation.get_value(obj_attr_list["lifetime"]))

            self.create_vm_list_for_ai(obj_attr_list)
     
            self.speculative_admission_control(obj_attr_list)

            _post_speculative_admission_control = True

            self.osci.pending_object_set("AI", obj_attr_list["uuid"], "Creating VMs: Switch tabs for tracking..." )
            if obj_attr_list["vm_creation"].lower() == "explicit" : 
                _status, _fmsg = self.parallel_obj_operation("attach", obj_attr_list)

            if not _status :
                _vm_uuid_list = obj_attr_list["vms"].split(',')
                obj_attr_list["vms"] = ''
                for _vm_uuid in _vm_uuid_list :
                    _vm_attr_list = self.osci.get_object("VM", False, _vm_uuid, False)
                    obj_attr_list["vms"] += _vm_uuid + '|' + _vm_attr_list["role"] + '|' + _vm_attr_list["name"] + ','
    
                obj_attr_list["vms"] = obj_attr_list["vms"][:-1]

                _status = 0

        except KeyboardInterrupt :
            _status = 42
            _fmsg = "CTRL-C interrupt"
            cbdebug("VM objects need to be aborted...", True)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = 41
            _fmsg = str(obj.msg)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ValueGeneration.ValueGenerationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except DataOpsException, obj :
            _status = 43
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AI pre-attachment operations failure: " + _fmsg
                cberr(_msg)

                if _post_speculative_admission_control :
                    self.admission_control("AI", obj_attr_list, "rollbackattach")
                
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AI pre-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def pre_attach_aidrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100

            _fmsg = "An error has occurred, but no error message was captured"

            _vmc_list = self.osci.get_object_list("VMC")
            if not _vmc_list :
                _msg = "No VMC attached to this experiment. Please "
                _msg += "attach at least one VMC, or the VM creations triggered "
                _msg += "by the AIs instantiated by the AS attachment operation "
                _msg += "will fail."
                raise self.oscc.ObjectStoreMgdConnException(_msg, _status)
                            
            _aidrs_templates = self.osci.get_object("GLOBAL", False, \
                                                 "aidrs_templates", False)
            _aidrs_templates["patterns"] = []
            for _element in _aidrs_templates :
                if _element.count("iait") :
                    _aidrs_templates["patterns"].append(_element[0:-5])
            
            obj_attr_list["nr_ais"] = 0
            obj_attr_list["arrival"] = int(time())

            if obj_attr_list["pattern"] in _aidrs_templates["patterns"] :
                # This is just a trick to remove the application name from the
                # start of the AIDRS attributes on the template. 
                # For instance, instead of adding the key  "simpledt_max_ais"
                # to the list of attributes of the AS we want the key to be in fact 
                # only "max_ais"
                _x = len(obj_attr_list["pattern"]) + 1
            
                for _key, _value in _aidrs_templates.iteritems() :
                    if _key.count(obj_attr_list["pattern"]) :
                        if _key[_x:] in obj_attr_list : 
                            if obj_attr_list[_key[_x:]] == "default" :
                                obj_attr_list[_key[_x:]] = _value
                        else :
                            obj_attr_list[_key[_x:]] = _value
                _status = 0
            else :
                _fmsg = "Unknown pattern: " + obj_attr_list["pattern"] 

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = 40
            _fmsg = str(obj.msg)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AIDRS pre-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AIDRS pre-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def pre_attach_vmcrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100

            _fmsg = "An error has occurred, but no error message was captured"
                            
            _vmcrs_templates = self.osci.get_object("GLOBAL", False, \
                                                    "vmcrs_templates", False)
            cbdebug(_vmcrs_templates["patterns"])
            if _vmcrs_templates["patterns"].count(obj_attr_list["pattern"]) :
                # This is just a trick to remove the application name from the
                # start of the AIDRS attributes on the template. 
                # For instance, instead of adding the key "simpledt_max_ais"
                # to the list of attributes of the AS we want the key to be in fact 
                # only "max_ais"
                _x = len(obj_attr_list["pattern"]) + 1
            
                for _key, _value in _vmcrs_templates.iteritems() :
                    if _key.count(obj_attr_list["pattern"]) :
                        if _key[_x:] in obj_attr_list : 
                            if obj_attr_list[_key[_x:]] == "default" :
                                obj_attr_list[_key[_x:]] = _value
                        else :
                            obj_attr_list[_key[_x:]] = _value
                _status = 0
            else :
                _fmsg = "Unknown pattern: " + obj_attr_list["pattern"] 

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = 40
            _fmsg = str(obj.msg)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VMCRS pre-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMCRS pre-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace    
    def objattach(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        _status = 100
        _fmsg = "An error has occurred, but no error message was captured"
        threading.current_thread().abort = False 
        threading.current_thread().aborted = False
            
        if "uuid" not in obj_attr_list :
            obj_attr_list["uuid"] = str(uuid5(NAMESPACE_DNS, \
                               str(randint(0,1000000000000000000)))).upper()
        
        if command == "vm-attach" :
            for key in ["ai", "ai_name", "aidrs", "aidrs_name", "pattern", "type"] :
                if key not in obj_attr_list :
                    obj_attr_list[key] = "none"
            
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _obj_type = command.split('-')[0].upper()
            _result = {}

            _pre_attach = False
            _admission_control = False
            _vmcregister = False
            _vmcreate = False
            _svmcreate = False
            _aidefine = False
            _created_object = False
            _created_pending = False

            obj_attr_list["name"] = "undefined"
            obj_attr_list["cloud_ip"] = "undefined"
            obj_attr_list["cloud_hostname"] = "undefined"

            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

                if not _status :
                    self.osci.add_to_list(_obj_type, "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], int(time()))
                    self.osci.pending_object_set(_obj_type, obj_attr_list["uuid"], "Initializing...")
                    _created_pending = True
    
                    _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                        
                    _cld_conn = _cld_ops_class(self.pid, self.oscp, \
                                               obj_attr_list["instance"])
    
                    if _obj_type == "VMC" :
                        self.pre_attach_vmc(obj_attr_list)
    
                    elif _obj_type == "VM" :
                        self.pre_attach_vm(obj_attr_list)
    
                    elif _obj_type == "AI" :
                        self.pre_attach_ai(obj_attr_list)
    
                    elif _obj_type == "AIDRS" :
                        self.pre_attach_aidrs(obj_attr_list)
                        
                    elif _obj_type == "SVM" :
                        self.pre_attach_svm(obj_attr_list)
    
                    elif _obj_type == "VMCRS" :
                        self.pre_attach_vmcrs(obj_attr_list)
    
                    else :
                        _msg = "Unknown object: " + _obj_type
                        raise self.ObjectOperationsException(_msg, 28)
                    
                    _pre_attach = True
                    _admission_control = self.admission_control(_obj_type, \
                                                            obj_attr_list, \
                                                            "attach")
    
                    if _obj_type == "VMC" :
                        _status, _fmsg = _cld_conn.vmcregister(obj_attr_list)
                        _vmcregister = True
    
                    elif _obj_type == "VM" :
                        _status, _fmsg = _cld_conn.vmcreate(obj_attr_list)
                        _vmcreate = True
                        
                    elif _obj_type == "SVM" :
                        _status, _fmsg = _cld_conn.vmcreate(obj_attr_list)
                        _svmcreate = True
    
                    elif _obj_type == "AI" :
                        _status, _fmsg = _cld_conn.aidefine(obj_attr_list)
                        self.assign_roles(obj_attr_list)
                        _aidefine = True
    
                    elif _obj_type == "AIDRS" :
                        True
                    
                    elif _obj_type == "VMCRS" :
                        True
    
                    else :
                        False
    
                    if not _status :
    
                        if "lifetime" in obj_attr_list and not "submitter" in obj_attr_list :
                            if obj_attr_list["lifetime"] != "none" :
                                obj_attr_list["departure"] = obj_attr_list["lifetime"] +\
                                 obj_attr_list["arrival"]
                        
                        if _obj_type == "VM" and "host_name" in obj_attr_list and obj_attr_list["host_name"] != "unknown" :
                            _host_attr_list = self.osci.get_object("HOST", True, obj_attr_list["host_name"], False)
                            obj_attr_list["host"] = _host_attr_list["uuid"]
    
                        self.osci.create_object(_obj_type, obj_attr_list["uuid"], \
                                                obj_attr_list, False, True)
                        _created_object = True
    
                        if _obj_type == "VMC" :
                            self.post_attach_vmc(obj_attr_list)
    
                        elif _obj_type == "SVM" :
                            self.post_attach_svm(_cld_conn, obj_attr_list)
                            
                        elif _obj_type == "VM" :
                            self.post_attach_vm(obj_attr_list)
    
                        elif _obj_type == "AI" :
                            self.post_attach_ai(obj_attr_list)
    
                        elif _obj_type == "AIDRS" :
                            self.post_attach_aidrs(obj_attr_list)
    
                        elif _obj_type == "VMCRS" :
                            self.post_attach_vmcrs(obj_attr_list)
    
                        else :
                            True

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:
            unique_state_key = "-attach-" + str(time())
            if _status :
                _msg = _obj_type + " object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") could not be "
                _msg += "attached to this experiment: " + _fmsg
                cberr(_msg)

                if self.osci :
                    self.osci.update_counter(_obj_type, "FAILED", "increment")
    
                    if _obj_type == "VM" or _obj_type == "SVM" :
                        if "mgt_001_provisioning_request_originated" in obj_attr_list :
                            obj_attr_list["mgt_999_provisioning_request_failed"] = \
                                int(time()) - int(obj_attr_list["mgt_001_provisioning_request_originated"])
                        
                    _xmsg = "Now all actions executed during the object's "
                    _xmsg += "attachment will be rolled back."
                    cberr(_xmsg)

                    if _obj_type == "AI" :
                        if "vms" in obj_attr_list :
                            self.destroy_vm_list_for_ai(obj_attr_list)
                            if obj_attr_list["vm_destruction"].lower() == "explicit" \
                            and obj_attr_list["destroy_vms"] != "0" :
                                self.parallel_obj_operation("detach", obj_attr_list)
                                
                        if obj_attr_list["action_after_vm_attach"] == "pause" :
                            self.osci.publish_message("VM", "pause_on_attach", obj_attr_list["uuid"] + ";error;" + _msg, 1, 3600)


                    if _obj_type == "VM" :
                        if "cloud_name" in obj_attr_list :
                            self.record_management_metrics(obj_attr_list["cloud_name"], \
                                                           "VM", obj_attr_list, "attach")
                        if obj_attr_list["action_after_vm_attach"] == "pause" :
                            uuid = obj_attr_list["ai"] if obj_attr_list["ai"] != "none" else obj_attr_list["uuid"]
                            self.osci.publish_message("VM", "pause_on_attach", uuid + ";error;" + _msg, 1, 3600)

                    if _admission_control or ( _obj_type == "AI" and _pre_attach) :
                        self.admission_control(_obj_type, obj_attr_list, \
                                               "rollbackattach")
                    if _vmcregister :
                        _cld_conn.vmcunregister(obj_attr_list)
    
                    if _vmcreate :
                        _cld_conn.vmdestroy(obj_attr_list)
                        self.auto_free_vm_port("qemu_debug", obj_attr_list)
                        
                    if _svmcreate :
                        _cld_conn.vmdestroy(obj_attr_list)
                        self.auto_free_vm_port("replication", obj_attr_list)
                        self.auto_free_vm_port("svm_qemu_debug", obj_attr_list)
                        self.auto_free_vm_port("rdma", obj_attr_list)
    
                    if _aidefine :
                        _cld_conn.aiundefine(obj_attr_list)
    
                    if _created_object :
                        self.osci.destroy_object(_obj_type, obj_attr_list["uuid"], \
                                                obj_attr_list, False)
    
                    obj_attr_list["tracking"] = _fmsg
                    if "uuid" in obj_attr_list :
                        self.osci.create_object("FAILEDTRACKING" + _obj_type, obj_attr_list["uuid"] + unique_state_key, \
                                                obj_attr_list, False, True, 3600)
                    _xmsg = "Done "
                    cberr(_xmsg)                

            else :
                _result = copy.deepcopy(obj_attr_list)
                self.osci.update_counter(_obj_type, "ARRIVED", "increment")
                
                _msg = _obj_type + " object " + obj_attr_list["uuid"] 
                _msg += " (named \"" + obj_attr_list["name"] +  "\") sucessfully "
                _msg += "attached to this experiment."
                
                if not "submitter" in obj_attr_list and _obj_type != "SVM":
                    _msg += " It is ssh-accessible at the IP address " + obj_attr_list["cloud_ip"]
                    _msg += " (" + obj_attr_list["cloud_hostname"] + ")."
                cbdebug(_msg)
                obj_attr_list["tracking"] = "Attach: success." 
                self.osci.create_object("FINISHEDTRACKING" + _obj_type, obj_attr_list["uuid"] + unique_state_key, \
                                        obj_attr_list, False, True, 3600)

            if _created_pending :
                self.osci.pending_object_remove(_obj_type, obj_attr_list["uuid"])
                self.osci.remove_from_list(_obj_type, "PENDING",obj_attr_list["uuid"] + "|" + obj_attr_list["name"], True)
                
            return self.package(_status, _msg, _result)

    @trace
    def post_attach_vmc(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            self.conn_check()

            if "hosts" in obj_attr_list and len(obj_attr_list["hosts"]) :
                for _host_uuid in obj_attr_list["hosts"].split(',') :
                    self.osci.create_object("HOST", _host_uuid, \
                                            obj_attr_list["host_list"][_host_uuid], False, True)
                    self.record_management_metrics(obj_attr_list["cloud_name"], \
                                                   "HOST", obj_attr_list["host_list"][_host_uuid], "attach")

                if not "attach_parallel" in obj_attr_list :
                    self.update_host_os_perfmon()
                elif obj_attr_list["attach_parallel"].lower() != "true" :
                    self.update_host_os_perfmon()

            self.record_management_metrics(obj_attr_list["cloud_name"], "VMC", \
                                           obj_attr_list, "attach")

            _status = 0

        except IndexError, msg :
            _status = 40
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        finally :
            if _status :
                _msg = "VMC post-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMC post-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_attach_svm(self, cld_conn, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            self.conn_check()
            self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                              False, "svm_stub_uuid", \
                                              obj_attr_list["uuid"], False)
            
            self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                              False, "svm_stub_name", \
                                              obj_attr_list["name"], False)
            
            self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                              False, "svm_stub_vmc", \
                                              obj_attr_list["vmc"], False)

            self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                              False, "svm_stub_ip", \
                                              obj_attr_list["svm_stub_ip"], False)

            _msg = "SVM FT Stub created, starting replication..."
            cbdebug(_msg, True)
            
            if obj_attr_list["model"].lower() != "sim" :
                _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])
                _status, _fmsg = cld_conn.vmreplicate_start(obj_attr_list)
            
            _status = 100
            
            if obj_attr_list["ai"] != "none" :
                _replicated_vms = int(self.get_object_attribute( \
                        obj_attr_list["cloud_name"], "AI", obj_attr_list["ai"], \
                            "replicated_vms"))
            
                self.osci.update_object_attribute("AI", obj_attr_list["ai"], \
                          False, "replicated_vms", _replicated_vms + 1, False)
                
            _status = 0
            
        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            
        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "SVM post-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "SVM post-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_attach_vm(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            if obj_attr_list["cloud_ip"] == "undefined" :
                _msg = "VM creation previously failed. Will not send files"
                _status = 452
                raise self.ObjectOperationException(_msg, _status)

            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _curr_tries = 0
            _start = int(time())
            _max_tries = int(obj_attr_list["update_attempts"])
            _output_list = []
            while _curr_tries < _max_tries :
                if "async" not in obj_attr_list or obj_attr_list["async"].lower() == "false" :
                    if threading.current_thread().abort :
                        _msg = "VM creation aborted during transfer file step..."
                        _status = 12345
                        raise self.ObjectOperationException(_msg, _status)
    
                if obj_attr_list["transfer_files"].lower() != "false" :
                    _cmd = "ssh -i " + obj_attr_list["identity"]
                    _cmd += " -o StrictHostKeyChecking=no"
                    _cmd += " -o UserKnownHostsFile=/dev/null " 
                    _cmd += obj_attr_list["login"] + "@"
                    _cmd += obj_attr_list["cloud_ip"] + " \"mkdir -p ~/cbtool;"
                    _cmd += "echo '#OSKN-" + self.oscp["kind"] + "' > ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSHN-" + self.oscp["hostname"] + "' >> ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSPN-" + self.oscp["port"] + "' >> ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSDN-" + self.oscp["database"] + "' >> ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSTO-" + self.oscp["timeout"] + "' >> ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSCN-" + obj_attr_list["cloud_name"] + "' >> ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSMO-integrated' >> ~/cb_os_parameters.txt;"
                    _cmd += "echo '#OSOI-" + obj_attr_list["instance"] + "' >> ~/cb_os_parameters.txt\";"

                    _cmd += "rsync -e \"ssh -o StrictHostKeyChecking=no -l " + obj_attr_list["login"] + " -i " 
                    _cmd += obj_attr_list["identity"] + "\" --exclude-from "
                    _cmd += "'" +  obj_attr_list["exclude_list"] + "' -az " + obj_attr_list["base_dir"] + "/* " 
                    _cmd += obj_attr_list["cloud_ip"] + ":~/cbtool/"

                    _msg = "RSYNC: " + _cmd
                    cbdebug(_msg)

                    _msg = "Sending files to " + obj_attr_list["name"] + " ("+ obj_attr_list["cloud_ip"] + ")..."
                    cbdebug(_msg, True)

                    _proc_h = Popen(_cmd, bufsize=-1, shell=True, stdout=PIPE, stderr=PIPE) 
                    if not _proc_h :
                        _msg = "Failed to create subprocess with " + _cmd
                        _curr_tries = _curr_tries + 1
                        sleep(int(obj_attr_list["inter_connection_attempts_time"]))
                    else :
                        if wait_on_process(self.pid, _proc_h, _output_list) :
                            _status = 0
                            break
                        else :
                            _curr_tries = _curr_tries + 1
                            sleep(int(obj_attr_list["update_frequency"]))
                else :
                    _output_list = []
                    _status = 0
                    break
                
            if _curr_tries >= _max_tries :
                _fmsg = "Unable to connect to VM after " + str(_max_tries)
                _fmsg += "tries. The VM seems unreachable."
            else :

                self.conn_check()
                _delay = int(time()) - _start
                self.osci.pending_object_set("VM", obj_attr_list["uuid"], "Files transferred...")
                obj_attr_list["mgt_005_file_transfer"] = _delay
                self.osci.update_object_attribute("VM", obj_attr_list["uuid"], \
                                                  False, "mgt_005_file_transfer", \
                                                  _delay)

                if "ai" in obj_attr_list and obj_attr_list["ai"] == "none" :
                    if not access(obj_attr_list["identity"], F_OK) :
                        obj_attr_list["identity"] = obj_attr_list["identity"].replace(obj_attr_list["username"], obj_attr_list["login"])

                    if "run_scripts" in obj_attr_list and obj_attr_list["run_scripts"].lower() != "false" :
                        _msg = "Performing generic VM post_boot configuration ..."
                        cbdebug(_msg, True)
                        if not repeated_ssh(self.pid, ["VM"], [obj_attr_list["name"]], \
                                               [obj_attr_list["cloud_ip"]], \
                                               [obj_attr_list["login"]], [None], \
                                               [obj_attr_list["identity"]], \
                                               ["~/cbtool/scripts/common/cb_post_boot.sh"], \
                                               None, obj_attr_list, "execute") :
                            _status = 1495
                            _fmsg = "Failure while executing remote post_boot scripts."
                        else :
                            _status = 0
                        
                    self.record_management_metrics(obj_attr_list["cloud_name"], \
                                                   "VM", obj_attr_list, "attach")

            if obj_attr_list["action_after_vm_attach"] == "pause" :
                uuid = obj_attr_list["ai"] if obj_attr_list["ai"] != "none" else obj_attr_list["uuid"]
                self.osci.publish_message("VM", "pause_on_attach", uuid + ";vmfinished;" + dic2str(obj_attr_list), 1, 3600)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            
        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                if len(_output_list) > 0 :
                    _fmsg = _output_list[0]
                _msg = "VM post-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VM post-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_attach_ai(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            if "run_scripts" in obj_attr_list and obj_attr_list["run_scripts"].lower() != "false" :
                self.osci.pending_object_set("AI", obj_attr_list["uuid"], "Running VM Applications..." )
                _status, _fmsg  = self.parallel_vm_config_for_ai(obj_attr_list["cloud_name"], \
                                                                 obj_attr_list["uuid"], "setup")
            else :
                _status = 0

            if not _status :
                self.record_management_metrics(obj_attr_list["cloud_name"], \
                                               "AI", obj_attr_list, "attach")

                if "save_on_attach" in obj_attr_list and obj_attr_list["save_on_attach"].lower() == "true" :

                    secs = int(obj_attr_list["seconds_before_save"].strip())
                    _msg = "Going to save VMs for AI " + obj_attr_list["name"] + " now..."
                    cbdebug(_msg, True)
                    
                    if secs > 0 :
                        try :
                            _msg = "Will wait " + str(secs) + " seconds to reach steady state before saving..."
                            cbdebug(_msg, True)
                            self.osci.pending_object_set("AI", obj_attr_list["uuid"], _msg)
                            sleep(secs)
                            _msg = "Saving " + obj_attr_list["name"] + " now...."
                            cbdebug(_msg)
                            self.osci.pending_object_set("AI", obj_attr_list["uuid"], _msg)
                            
                        except KeyboardInterrupt :
                            _fmsg = "CTRL-C: Cancelled VM save..."
                            raise self.ObjectOperationException(_fmsg, 195)

                    obj_attr_list["target_state"] = "save"
                    _status = self.airunstate_actual(obj_attr_list)
                    self.runstate_list_for_ai(obj_attr_list, "save")
                    if obj_attr_list["state_changed_vms"] != "0" :
                        _status, _fmsg = self.parallel_obj_operation("runstate", obj_attr_list)

                elif obj_attr_list["run_scripts"].lower() == "false" :
                    _status = 0

                else :
                    _status = 0
                    
            if obj_attr_list["action_after_vm_attach"] == "pause" :
                self.osci.publish_message("VM", "pause_on_attach", obj_attr_list["uuid"] + ";appfinished;" + dic2str(obj_attr_list), 1, 3600)

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if "target_state" in obj_attr_list :
                del obj_attr_list["target_state"]
            if _status :
                _msg = "AI post-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AI post-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_attach_aidrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"            

            _cmd = self.path + "/cbact"
            _cmd += " --procid=" + self.pid
            _cmd += " --osp=" + dic2str(self.oscp)
            _cmd += " --uuid=" + obj_attr_list["uuid"] 
            _cmd += " --operation=aidr-submit"
            _cmd += " --cn=" + obj_attr_list["cloud_name"]
            _cmd += " --daemon"
            #_cmd += "  --debug_host=127.0.0.1"

            _proc_h = Popen(_cmd, shell=True, stdout=PIPE, stderr=PIPE)

            if _proc_h.pid :
                _msg = "AIDRS attachment command \"" + _cmd + "\" "
                _msg += " was successfully started."
                _msg += "The process id is " + str(_proc_h.pid) + "."
                cbdebug(_msg)

                _obj_id = obj_attr_list["uuid"] + '-' + "submit"
                self.update_process_list(self.cn, "AIDRS", _obj_id, \
                                         str(_proc_h.pid), "add")

            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AIDRS post-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AIDRS post-attachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_attach_vmcrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"            

            _cmd = self.path + "/cbact"
            _cmd += " --procid=" + self.pid
            _cmd += " --osp=" + dic2str(self.oscp)
            _cmd += " --uuid=" + obj_attr_list["uuid"] 
            _cmd += " --operation=vmcr-submit"
            _cmd += " --cn=" + obj_attr_list["cloud_name"]
            _cmd += " --daemon"

            _proc_h = Popen(_cmd, shell=True, stdout=PIPE, stderr=PIPE)

            if _proc_h.pid :
                _msg = "VMCRS attachment command \"" + _cmd + "\" "
                _msg += " was successfully started."
                _msg += "The process id is " + str(_proc_h.pid) + "."
                cbdebug(_msg)

                _obj_id = obj_attr_list["uuid"] + '-' + "submit"
                self.update_process_list(self.cn, "VMCRS", _obj_id, str(_proc_h.pid), "add")

            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VMCRS post-attachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMCRS post-attachment operations success."
                cbdebug(_msg)
                return _status, _msg
        
    @trace
    def pre_detach_vmc(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            _reservations_on_vmc = int(obj_attr_list["nr_vms"])
            
            if _reservations_on_vmc and obj_attr_list["force_detach"] == "false" :
                _status = 46
                _fmsg = "This VMC has " + str(_reservations_on_vmc) + " VM "
                _fmsg += "reservations. Please detach all VMs on this VMC before"
                _fmsg += " proceeding."
            else :
                _status = 0

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            
        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VMC pre-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMC pre-detachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_detach_vm(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"            

            if obj_attr_list["ai"] == "none" or obj_attr_list["force_detach"].lower() != "false" :
                _status = 0
            else :
                _status = 46
                _fmsg = "This VM is part of the AI " + obj_attr_list["ai"] + '.'
                _fmsg += "Please detach this AI instead."
                
            if not _status :
                if obj_attr_list["svm_stub_vmc"].strip().lower() == "none" :
                    _status = 0
                elif obj_attr_list["force_detach"].lower() != "false" :
                    _msg = "Will forcefully stop replication before destroying primary VM..."
                    cbdebug(_msg, True)
                    svm_obj_attr_list = self.osci.get_object("SVM", False, obj_attr_list["svm_stub_uuid"], False)
                    _status, _fmsg, _unused = self.objdetach(svm_obj_attr_list, svm_obj_attr_list["name"], "svm-detach")
                else :
                    _status = 47
                    _fmsg = "SVM FT Replication must be stopped before detachment."

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VM pre-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VM pre-detachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_detach_svm(self, cld_conn, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"            

            _exists = self.osci.object_exists("VM", obj_attr_list["primary_uuid"], False) 
            if not _exists :
                _msg = "SVM FT stub's primary VM is gone. Just going to cleanup..."
                cbwarn(_msg, True)
                obj_attr_list["primary_uuid"] = "none"
                _status = 0
            elif obj_attr_list["failover"] != "false" :
                _msg = "Going to kill primary and failover to stub..."
                cbdebug(_msg, True)
                _status = 0
            else :
                _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])
                _msg = "Going to disable replication to stub " + obj_attr_list["name"] + "..."
                cbdebug(_msg, True)
                # Put back when disabling works.....
                #_status, _fmsg = cld_conn.vmreplicate_stop(obj_attr_list)
                _status = 0

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            
        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "SVM pre-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "SVM pre-detachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_detach_ai(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            '''
            if int(obj_attr_list["replicated_vms"]) > 0 and obj_attr_list["force_detach"].lower() == "false" :
                _msg = "Some VMs are FT replicated. Please deactivate replication first."
                raise self.ObjectOperationException(_msg, 143)
            '''
            
            if obj_attr_list["aidrs"] == "none" or\
             obj_attr_list["force_detach"].lower() != "false" or \
             not self.osci.object_exists("AIDRS", obj_attr_list["aidrs"], False) :

                _msg = "Removing AI from the \"BYAIDRS\" view"
                cbdebug(_msg)
                self.osci.remove_from_view("AI", obj_attr_list, "BYAIDRS")

                _msg = "Destroying all VMs belonging to this AI"
                cbdebug(_msg)
                self.destroy_vm_list_for_ai(obj_attr_list)

                if obj_attr_list["vm_destruction"].lower() == "explicit" and obj_attr_list["destroy_vms"] != "0" :
                    _status, _fmsg = self.parallel_obj_operation("detach", obj_attr_list)
                else :
                    _status = 0
            else :
                _status = 46
                _fmsg = "This AI is part of the AIDRS " + obj_attr_list["aidrs"] + '.'
                _fmsg += "Please detach this AS instead."

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            
        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AI pre-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AI pre-detachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_detach_aidrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AIDRS pre-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AIDRS pre-detachment operations success."
                cbdebug(_msg)
                return True

    @trace
    def pre_detach_vmcrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _status = 0

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VMCRS pre-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMCRS pre-detachment operations success."
                cbdebug(_msg)
                return True

    @trace    
    def objdetach(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        
        threading.current_thread().abort = False 
        threading.current_thread().aborted = False
        
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _obj_type = command.split('-')[0].upper()
            _result = {}
             
            _admission_control = False
            _detach_pending = False

            obj_attr_list["uuid"] = "undefined"
            obj_attr_list["name"] = "undefined"
            
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

            if not _status :
                self.osci.add_to_list(_obj_type, "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], int(time()))
                self.osci.pending_object_set(_obj_type, obj_attr_list["uuid"], "Detaching...")
                _detach_pending = True
                
                _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                _cld_conn = _cld_ops_class(self.pid, self.oscp, \
                                           obj_attr_list["instance"])

                obj_attr_list["current_state"] = \
                self.osci.get_object_state(_obj_type, obj_attr_list["uuid"])

                _admission_control = self.admission_control(_obj_type, \
                                                            obj_attr_list, \
                                                            "detach")

                if _obj_type == "VMC" :
                    self.pre_detach_vmc(obj_attr_list)
                    _status, _msg = _cld_conn.vmcunregister(obj_attr_list)

                elif _obj_type == "VM" :
                    self.pre_detach_vm(obj_attr_list)
                    _status, _msg = _cld_conn.vmdestroy(obj_attr_list)

                elif _obj_type == "SVM" :
                    self.pre_detach_svm(_cld_conn, obj_attr_list)
                    if obj_attr_list["failover"] == "false" :
                        _status, _msg = _cld_conn.vmdestroy(obj_attr_list)
                    else :
                        _primary = self.osci.get_object("VM", False, obj_attr_list["primary_uuid"], False)
                        _primary["svm_stub_ip"] = "undefined" 
                        _primary["mgt_901_deprovisioning_request_originated"] = int(time())
                        _status, _msg = _cld_conn.vmdestroy(_primary)
                        if not _status :
                            if obj_attr_list["model"].lower() != "sim" :
                                _status, _msg = _cld_conn.vmreplicate_resume(obj_attr_list)
                            else :
                                _status = 0
                                _msg = "FT Resume success"

                elif _obj_type == "AI" :
                    self.pre_detach_ai(obj_attr_list)

                elif _obj_type == "AIDRS" :
                    self.pre_attach_aidrs(obj_attr_list)

                elif _obj_type == "VMCRS" :
                    self.pre_attach_vmcrs(obj_attr_list)

                else :
                    _msg = "Unknown object: " + _obj_type
                    raise self.ObjectOperationException(_msg, 28)

                if not _status :
                    self.osci.destroy_object(_obj_type, obj_attr_list["uuid"], \
                                             obj_attr_list, False)

                    _destroyed_object = True

                    if _obj_type == "VMC" :
                        self.post_detach_vmc(obj_attr_list)

                    elif _obj_type == "VM" :
                        self.post_detach_vm(obj_attr_list)

                    elif _obj_type == "SVM" :
                        self.post_detach_svm(_cld_conn, obj_attr_list)

                    elif _obj_type == "AI" :
                        self.post_detach_ai(obj_attr_list)

                    elif _obj_type == "AIDRS" :
                        self.post_detach_aidrs(obj_attr_list)

                    elif _obj_type == "VMCRS" :
                        self.post_detach_vmcrs(obj_attr_list)

                    else :
                        True

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg =  str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:

            if _status :
                _msg = _obj_type + " object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") could not be "
                _msg += "detached from this experiment: " + _fmsg
                cberr(_msg)

                if self.osci :
                    self.osci.update_counter(_obj_type, "FAILED", "increment")
    
                    if _admission_control :
                        self.admission_control(_obj_type, obj_attr_list, \
                                               "rollbackdetach")
                        
                    obj_attr_list["tracking"] = _fmsg
            else :
                _result = copy.deepcopy(obj_attr_list)
                self.osci.update_counter(_obj_type, "DEPARTED", "increment")
                
                _msg = _obj_type + " object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") was "
                _msg += "sucessfully detached from this experiment."
                cbdebug(_msg)
                obj_attr_list["tracking"] = "Detach: success." 
                
            unique_state_key = "-detach-" + str(time())
            if self.osci and obj_attr_list["uuid"] != "undefined":
                tracking = "FINISHED" if not _status else "FAILED"
                self.osci.create_object(tracking + "TRACKING" + _obj_type, obj_attr_list["uuid"] + unique_state_key, \
                                        obj_attr_list, False, True, 3600)
                if _detach_pending :
                    self.osci.pending_object_remove(_obj_type, obj_attr_list["uuid"])
                    self.osci.remove_from_list(_obj_type, "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], True)
            
            return self.package(_status, _msg, _result)

    @trace
    def post_detach_vmc(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            self.conn_check()

            if "hosts" in obj_attr_list :
                for _host_uuid in obj_attr_list["hosts"].split(',') :
                    _host_attr_list =  self.osci.get_object("HOST", False, _host_uuid, False)
                    self.osci.destroy_object("HOST", _host_uuid, _host_attr_list, False)
                    self.record_management_metrics(obj_attr_list["cloud_name"], \
                                                   "HOST", _host_attr_list, \
                                                   "detach")

            self.record_management_metrics(obj_attr_list["cloud_name"], "VMC", \
                                           obj_attr_list, "detach")
            
            _status = 0

        except IndexError, msg :
            _status = 40
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VMC post-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VMC post-detachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_detach_svm(self, cld_conn, obj_attr_list) :
        '''
        TBD
        '''
        
        self.auto_free_vm_port("replication", obj_attr_list)
        self.auto_free_vm_port("svm_qemu_debug", obj_attr_list)
        self.auto_free_vm_port("rdma", obj_attr_list)
        
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            
            if obj_attr_list["primary_uuid"] != "none" :
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "svm_stub", "false", False)
                
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "svm_stub_uuid", "", False)
                
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "svm_stub_name", "", False)
                
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "svm_stub_vmc", "none", False)
                
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "svm_stub_ip", "none", False)
                
                if obj_attr_list["ai"] != "none" :
                    _replicated_vms = int(self.get_object_attribute( \
                            obj_attr_list["cloud_name"], "AI", obj_attr_list["ai"], \
                                "replicated_vms"))
                
                    self.osci.update_object_attribute("AI", obj_attr_list["ai"], \
                              False, "replicated_vms", _replicated_vms - 1, False)
                
            if obj_attr_list["failover"] != "false" :
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "vmc", obj_attr_list["vmc"], False)
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "vmc_name", obj_attr_list["vmc_name"], False)
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "vmc_cloud_ip", obj_attr_list["vmc_cloud_ip"], False)
                self.osci.update_object_attribute("VM", obj_attr_list["primary_uuid"], \
                                                  False, "vmc_pool", obj_attr_list["vmc_pool"], False)
                
            _status = 0

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg =  str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VM post-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VM post-detachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_detach_vm(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            self.auto_free_vm_port("qemu_debug", obj_attr_list)
            self.conn_check()

            if obj_attr_list["current_state"] != "attached" :

                if "post_capture" in obj_attr_list and obj_attr_list["post_capture"] == "true" :
                    _scores = True
                else :
                    _scores = False
                self.osci.remove_from_list("VM", "VMS_UNDERGOING_" + obj_attr_list["current_state"].upper(), obj_attr_list["uuid"], _scores)

            self.record_management_metrics(obj_attr_list["cloud_name"], "VM", \
                                           obj_attr_list, "detach")

            _status = 0

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg =  str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "VM post-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "VM post-detachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_detach_ai(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"


            if obj_attr_list["current_state"] != "attached" :
                if "pre_capture" in obj_attr_list and obj_attr_list["pre_capture"] == "true" :
                    _scores = True
                else :
                    _scores = False
                self.osci.remove_from_list("AI", "AIS_UNDERGOING_" + \
                                           obj_attr_list["current_state"].upper(), \
                                           obj_attr_list["uuid"], _scores)

            _status = 0

        except IndexError, msg :
            _status = 40
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AI post-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AI post-detachment operations success."
                cbdebug(_msg)
                return _status, _msg

    @trace
    def post_detach_aidrs(self, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            
            _status = 0

        except IndexError, msg :
            _status = 40
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "AIDRS post-detachment operations failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                _msg = "AIDRS post-detachment operations success."
                cbdebug(_msg)
                return _status, _msg

    def svmstat(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _obj_type = command.split('-')[0].upper()
             
            obj_attr_list["name"] = "undefined"
            
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)
                
            _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
            _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])
            
            cbdebug("Going to show SVM progress. CTRL-C to quit", True) 
            
            _primary_obj_attribs = self.osci.get_object("VM", False, \
                                    obj_attr_list["primary_uuid"], False)
            while True :
                unused, _msg = _cld_conn.vmreplicate_status(_primary_obj_attribs)
                cbdebug(_msg, True)
                sleep(3)
            
        except KeyboardInterrupt :
            _status = 0
            
        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)
            
        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        finally :
            _msg = "SVM statistics "
            if _status :
                _msg += "failure: " + _fmsg
            else :
                _msg += "success."
            return _status, _msg, None

    @trace
    def objdetachall(self, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _smsg = ''

            _obj_attr_list = {}

            _obj_type = command.split('-')[0].upper()

            if BaseObjectOperations.default_cloud is None :
                _obj_attr_list["cloud_name"] = parameters.split()[0]
            else :
                if len(parameters) > 0 and BaseObjectOperations.default_cloud == parameters.split()[0] :
                    True
                else :
                    parameters = BaseObjectOperations.default_cloud + ' ' + parameters
                _obj_attr_list["cloud_name"] = parameters.split()[0]           

            if self.cn == _obj_attr_list["cloud_name"] :
                True
            else :
                self.cn = _obj_attr_list["cloud_name"]
                self.oscp["cloud_name"] = self.cn
                #self.osci.disconnect()
                self.osci = False

            self.conn_check()

            _obj_attr_list["command_originated"] = int(time())
            _obj_attr_list["command"] = _obj_type.lower() + "detach " + _obj_attr_list["cloud_name"] + " all"
            _obj_attr_list["name"] = "all"

            _obj_defaults = self.osci.get_object("GLOBAL", False, \
                                                 _obj_type.lower() + "_defaults", \
                                                 False)

            _obj_attr_list["detach_parallelism"] = _obj_defaults["detach_parallelism"] 

            self.get_counters(_obj_attr_list["cloud_name"], _obj_attr_list)
            self.record_management_metrics(_obj_attr_list["cloud_name"], _obj_type, _obj_attr_list, "trace")

            _obj_list = self.osci.get_object_list(_obj_type)

            if _obj_list :        
                _obj_counter = 0
                _obj_attr_list["parallel_operations"] = {}
                _obj_attr_list["parallel_operations"][_obj_counter] = {}
                for _obj in _obj_list :
                    _current_state = self.osci.get_object_state(_obj_type, _obj)
                    if _current_state == "attached" : 
                        _obj_name = self.osci.get_object(_obj_type, False, _obj, False)["name"]
                        _obj_attr_list["parallel_operations"][_obj_counter] = {}
                        _obj_attr_list["parallel_operations"][_obj_counter]["parameters"] = _obj_attr_list["cloud_name"]  + ' ' + _obj_name
                        _obj_attr_list["parallel_operations"][_obj_counter]["operation"] = _obj_type.lower() + "-detach"
                        _obj_attr_list["parallel_operations"][_obj_counter]["uuid"] = _obj
                        _obj_counter += 1

                _status, _fmsg = self.parallel_obj_operation("detach", _obj_attr_list)

            else :
                True

            if _obj_type == "VMC" :
                _cloud_parameters = self.get_cloud_parameters(_obj_attr_list["cloud_name"])
                _cloud_parameters["all_vmcs_attached"] = "false"
                self.update_cloud_attribute("all_vmcs_attached", "false")

                _proc_man = ProcessManagement(username = _cloud_parameters["username"], cloud_name = self.cn)
                _gmetad_pid = _proc_man.get_pid_from_cmdline("gmetad.py")

                if len(_gmetad_pid) :
                    cbdebug("Killing the running Host OS performance monitor (gmetad.py)......", True)
                    _proc_man.kill_process("gmetad.py", self.cn)

            _status = 0

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "All " + _obj_type + "s detachment failure: " + _fmsg
                cberr(_msg)
            else :
                _msg = "All " + _obj_type + "s successfully detached"
                cbdebug(_msg)
            return _status, _msg, None

    @trace    
    def vmcapture(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"

            obj_attr_list["uuid"] = "undefined"            
            obj_attr_list["name"] = "undefined"
            _capturable_vm = True

            _obj_type = command.split('-')[0].upper()

            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

                if not _status :
                    _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                    _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])
                    
                    if "vmcrs" in obj_attr_list :
                        self.osci.update_object_attribute("VMCRS", \
                                                          obj_attr_list["vmcrs"], \
                                                          False, "nr_vmcapreqs", \
                                                          1, True)
    
                    if "ai" in obj_attr_list and obj_attr_list["ai"].lower() != "none" :
                        _ai_attr_list = self.osci.get_object("AI", False, obj_attr_list["ai"], False)
                        
                        _current_state = self.osci.get_object_state("AI", obj_attr_list["ai"])
                        
                        if _current_state == "attached" :
    
                            self.osci.add_to_list("AI", "AIS_UNDERGOING_CAPTURE", obj_attr_list["ai"], int(time())) 
                            self.osci.set_object_state("AI", obj_attr_list["ai"], "capture")
                            
                            self.osci.add_to_list("VM", "VMS_UNDERGOING_CAPTURE", obj_attr_list["uuid"], int(time()))
                            self.osci.set_object_state("VM", obj_attr_list["uuid"], "capture")
    
                            _ai_attr_list["exclude_vm"] = obj_attr_list["uuid"]
                            _ai_attr_list["pre_capture"] = "true"            
                            self.objdetach(_ai_attr_list, _ai_attr_list["cloud_name"] + \
                                           ' ' + _ai_attr_list["name"], "ai-detach")
                        else :
                            _fmsg = "VM object named \"" + obj_attr_list["name"] + "\" could "
                            _fmsg += "not be captured because the AI it belongs to is on the "
                            _fmsg += "\"" + _current_state + "\" state."
                            _status = 78
                            _capturable_vm = False
    
                    else :
                        
                        _current_state = self.osci.get_object_state("VM", obj_attr_list["uuid"])
                        
                        if _current_state == "attached" :
                            
                            self.osci.add_to_list("VM", "VMS_UNDERGOING_CAPTURE", obj_attr_list["uuid"], int(time()))
                            self.osci.set_object_state("VM", obj_attr_list["uuid"], "capture")
    
                        else : 
                            _fmsg = "VM object named \"" + obj_attr_list["name"] + "\" could "
                            _fmsg += "not be captured because it is on the "
                            _fmsg += "\"" + _current_state + "\" state."
                            _status = 78
                            _capturable_vm = False
    
                    if _capturable_vm :
    
                        _status, _fmsg = _cld_conn.vmcapture(obj_attr_list)
     
                        self.conn_check()
    
                        self.osci.update_object_attribute("VM", \
                                                          obj_attr_list["uuid"], \
                                                          False, \
                                                          "mgt_101_capture_request_originated", \
                                                          obj_attr_list["mgt_101_capture_request_originated"])
    
                        self.osci.update_object_attribute("VM", \
                                                          obj_attr_list["uuid"], \
                                                          False, \
                                                          "mgt_102_capture_request_sent", \
                                                          obj_attr_list["mgt_102_capture_request_sent"])

                        if "mgt_103_capture_request_completed" in obj_attr_list :
                            self.osci.update_object_attribute("VM", \
                                                              obj_attr_list["uuid"], \
                                                              False, \
                                                              "mgt_103_capture_request_completed", \
                                                              obj_attr_list["mgt_103_capture_request_completed"])

                        else :
                            self.osci.update_object_attribute("VM", \
                                                              obj_attr_list["uuid"], \
                                                              False, \
                                                              "mgt_999_capture_request_failed", \
                                                              obj_attr_list["mgt_999_capture_request_failed"])
    
                        obj_attr_list["post_capture"] = "true"
    
                        self.objdetach(obj_attr_list, obj_attr_list["cloud_name"] + \
                                       ' ' + obj_attr_list["name"] + " true", \
                                       "vm-detach")
        
                        if "vmcrs" in obj_attr_list :
                            self.osci.update_object_attribute("VMCRS", \
                                                              obj_attr_list["vmcrs"], \
                                                              False, "nr_vmcapreqs", \
                                                              -1, True)
                        
                    _status = 0

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = _obj_type + " object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") could not be "
                _msg += "captured on this experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = _obj_type + " object " + obj_attr_list["uuid"] 
                _msg += " (named \"" + obj_attr_list["name"] +  "\") successfully captured "
                _msg += "on this experiment."
                cbdebug(_msg)

            return _status, _msg, None

    @trace    
    def vmresize(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            
            obj_attr_list["uuid"] = "undefined"            
            obj_attr_list["name"] = "undefined"

            _resizable_vm = True
            _obj_type = command.split('-')[0].upper()
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)
                
            if not _status :
                _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])

                if "ai" in obj_attr_list and obj_attr_list["ai"].lower() != "none" :
                    _ai_attr_list = self.osci.get_object("AI", False, obj_attr_list["ai"], False)
                    
                    _current_state = self.osci.get_object_state("AI", obj_attr_list["ai"])
                    
                    if _current_state == "attached" :

                        self.osci.add_to_list("AI", "AIS_UNDERGOING_RESIZE", obj_attr_list["ai"]) 
                        self.osci.set_object_state("AI", obj_attr_list["ai"], "resize")
                        
                        self.osci.add_to_list("VM", "VMS_UNDERGOING_RESIZE", obj_attr_list["uuid"])
                        self.osci.set_object_state("VM", obj_attr_list["uuid"], "resize")

                    else :
                        _fmsg = "VM object named \"" + obj_attr_list["name"] + "\" could "
                        _fmsg += "not be resized because the AI it belongs to is on the "
                        _fmsg += "\"" + _current_state + "\" state."
                        _status = 78
                        _resizable_vm = False

                else :
                    
                    _current_state = self.osci.get_object_state("VM", obj_attr_list["uuid"])
                    
                    if _current_state == "attached" :
                        
                        self.osci.add_to_list("VM", "VMS_UNDERGOING_RESIZE", obj_attr_list["uuid"])
                        self.osci.set_object_state("VM", obj_attr_list["uuid"], "resize")

                    else : 
                        _fmsg = "VM object named \"" + obj_attr_list["name"] + "\" could "
                        _fmsg += "not be resized because it is on the "
                        _fmsg += "\"" + _current_state + "\" state."
                        _status = 78
                        _resizable_vm = False

                if _resizable_vm :
                    obj_attr_list["resource_description"] = str2dic(obj_attr_list["resource_description"])
                    

                    _status, _fmsg = _cld_conn.vmresize(obj_attr_list)
                    
                    update = {}
                    self.osci.update_object_attribute("VM", \
                                                      obj_attr_list["uuid"], \
                                                      False, \
                                                      "mgt_301_resize_request_originated", \
                                                      obj_attr_list["mgt_301_resize_request_originated"])

                    self.osci.update_object_attribute("VM", \
                                                      obj_attr_list["uuid"], \
                                                      False, \
                                                      "mgt_302_resize_request_sent", \
                                                      obj_attr_list["mgt_302_resize_request_sent"])
                    
                    self.osci.update_object_attribute("VM", \
                                                      obj_attr_list["uuid"], \
                                                      False, \
                                                      "mgt_303_resize_request_completed", \
                                                      obj_attr_list["mgt_303_resize_request_completed"])

                    self.osci.remove_from_list("AI", "AIS_UNDERGOING_RESIZE", obj_attr_list["ai"])
                    self.osci.remove_from_list("VM", "VMS_UNDERGOING_RESIZE", obj_attr_list["uuid"])
                    self.osci.set_object_state("VM", obj_attr_list["uuid"], "attached")
                    self.osci.set_object_state("AI", obj_attr_list["ai"], "attached")

                    _status = 0

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "VM object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") could not be "
                _msg += "resized on this experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = "VM object " + obj_attr_list["uuid"] 
                _msg += " (named \"" + obj_attr_list["name"] +  "\") resized "
                _msg += "on this experiment."
                cbdebug(_msg)

            return _status, _msg, None

    @trace    
    def vmrunstate(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _result = {}
            _runstate_pending = False
            obj_attr_list["uuid"] = "undefined"            
            obj_attr_list["name"] = "undefined"
            obj_attr_list["target_state"] = "undefined"
                    
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)
                
                if not _status :
                    self.osci.add_to_list("VM", "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], int(time()))
                    self.osci.pending_object_set("VM", obj_attr_list["uuid"], "Changing state to: " + obj_attr_list["target_state"])
                    _runstate_pending = True
                    _current_state = self.osci.get_object_state("VM", obj_attr_list["uuid"])
    
                    _target_state = obj_attr_list["target_state"].lower()
                    if _target_state == "resume" :
                        _target_state = "attached"
    
                    if  _target_state not in ["save", "suspend", "fail", "attached" ] :
                        _fmsg = "Unknown state: " + _target_state
                        _status = 101
    
                    if not _status :
                        if _target_state != _current_state :
                            obj_attr_list["current_state"] = _current_state
                            _cld_ops_class = self.get_cloud_class(obj_attr_list["model"])
                            _cld_conn = _cld_ops_class(self.pid, self.oscp, obj_attr_list["instance"])
                                     
                            # TAC looks up libvirt function based on target state
                            # Do not remove this
                            if _target_state == "attached" :
                                if _current_state == "save" :
                                    obj_attr_list["target_state"] = "restore"
                                else :
                                    obj_attr_list["target_state"] = "resume"
                            elif _target_state == "fail" and _current_state != "attached" :
                                _msg = "Unable to fail a VM that is not on the \""
                                _msg += "attached\" state."
                                _status = 871
                                raise self.ObjectOperationException(_msg, _status)
                            
                            _status, _fmsg = _cld_conn.vmrunstate(obj_attr_list)
                            
                            if not _status :
                                self.osci.set_object_state("VM", obj_attr_list["uuid"], _target_state)
                                if _target_state != "attached" :
                                    self.osci.add_to_list("VM", "VMS_UNDERGOING_" + _target_state.upper(), obj_attr_list["uuid"])
                                elif _target_state == "attached" :
                                    self.osci.remove_from_list("VM", "VMS_UNDERGOING_SAVE", obj_attr_list["uuid"])
                                    self.osci.remove_from_list("VM", "VMS_UNDERGOING_SUSPEND", obj_attr_list["uuid"]) 
                                    self.osci.remove_from_list("VM", "VMS_UNDERGOING_FAIL", obj_attr_list["uuid"]) 
    
                                self.osci.update_object_attribute("VM", \
                                                                  obj_attr_list["uuid"], \
                                                                  False, \
                                                                  "mgt_201_runstate_request_originated", \
                                                                  obj_attr_list["mgt_201_runstate_request_originated"])
    
                                self.osci.update_object_attribute("VM", \
                                                                  obj_attr_list["uuid"], \
                                                                  False, \
                                                                  "mgt_202_runstate_request_sent", \
                                                                  obj_attr_list["mgt_202_runstate_request_sent"])
                                
                                self.osci.update_object_attribute("VM", \
                                                                  obj_attr_list["uuid"], \
                                                                  False, \
                                                                  "mgt_203_runstate_request_completed", \
                                                                  obj_attr_list["mgt_203_runstate_request_completed"])
                                
                                self.record_management_metrics(obj_attr_list["cloud_name"], "VM", obj_attr_list, "runstate")
                                _status = 0
                        else :
                            _msg = "VM is already at the \"" + obj_attr_list["target_state"]
                            _msg += "\" state. There is no need to explicitly change it."
                            cbdebug(_msg)
                            _status = 0

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except CldOpsException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:

            unique_state_key = "-state-" + str(time())
            if _status :
                if _status != 9 :
                    _msg = "VM object " + obj_attr_list["uuid"] + " ("
                    _msg += "named \"" + obj_attr_list["name"] + "\") could "
                    _msg += " not have his run state changed to \""
                    _msg += obj_attr_list["target_state"] + "\" on this "
                    _msg += "experiment: " + _fmsg
                    cberr(_msg)
                else :
                    _msg = _fmsg
                obj_attr_list["tracking"] = "Change state request: " + _fmsg
                if "uuid" in obj_attr_list and self.osci :
                    self.osci.create_object("FAILEDTRACKINGVM", \
                                            obj_attr_list["uuid"] + \
                                            unique_state_key, \
                                            obj_attr_list, False, True, 3600)
            else :
                _msg = "VM object " + obj_attr_list["uuid"] 
                _msg += " (named \"" + obj_attr_list["name"] +  "\") had "
                _msg += "its run state successfully changed to \""
                _msg += obj_attr_list["target_state"] + "\" on this "
                _msg += "experiment." 
                cbdebug(_msg)
                obj_attr_list["tracking"] = "Change state request: success." 
                self.osci.create_object("FINISHEDTRACKINGVM", obj_attr_list["uuid"] + unique_state_key, \
                                        obj_attr_list, False, True, 3600)
                
            if _runstate_pending :
                self.osci.pending_object_remove("VM", obj_attr_list["uuid"])
                self.osci.remove_from_list("VM", "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], True)

            return self.package(_status, _msg, _result)

    @trace
    def vmrunstateall(self, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _smsg = ''
            _obj_attr_list = {}
            _target_state = "undefined"
 
            if BaseObjectOperations.default_cloud is None :
                _obj_attr_list["cloud_name"] = parameters.split()[0]
            else :
                if len(parameters) > 0 and BaseObjectOperations.default_cloud == parameters.split()[0] :
                    True
                else :
                    parameters = BaseObjectOperations.default_cloud + ' ' + parameters
                _obj_attr_list["cloud_name"] = parameters.split()[0]           
            
            parameters = parameters.split()

            if len(parameters) >= 3 :
                _all = parameters[1]
                _target_state= parameters[2]
                _status = 0

            if len(parameters) < 3:
                _status = 9
                _fmsg = "Usage: vmrunstate <cloud name> <vm name> <runstate> [mode]"

            if self.cn == _obj_attr_list["cloud_name"] :
                True
            else :
                self.cn = _obj_attr_list["cloud_name"]
                self.oscp["cloud_name"] = self.cn
                #self.osci.disconnect()
                self.osci = False

            if not _status :    
                self.conn_check()

                _obj_attr_list["command_originated"] = int(time())
                _obj_attr_list["command"] = "vmrunstateall " + self.cn + " all"
                _obj_attr_list["name"] = "all"

                self.get_counters(_obj_attr_list["cloud_name"], _obj_attr_list)
                self.record_management_metrics(_obj_attr_list["cloud_name"], "VM", _obj_attr_list, "trace")

                _vm_list = False
                if _target_state == "attached" and command == "repair" :
                    _vm_list = self.osci.get_list("VM", "VMS_UNDERGOING_FAIL")

                elif _target_state == "attached" and command == "restore" :
                    _vm_list = self.osci.get_list("VM", "VMS_UNDERGOING_SAVE")

                else :
                    _vm_list = []
                    for _vm in self.osci.get_object_list("VM") :
                        _current_state = self.osci.get_object_state("VM", _vm)
                        if _current_state and _current_state == "attached" :
                            _vm_list.append(_vm)

                if _vm_list :
                    _vm_counter = 0
                    _obj_attr_list["parallel_operations"] = {}
                    _obj_attr_list["parallel_operations"][_vm_counter] = {} 
                    for _vm in _vm_list :
                        if len(_vm) == 2 :
                            _vm_uuid = _vm[0]
                        else :
                            _vm_uuid = _vm
                        _vm_attr_list = self.osci.get_object("VM", False, _vm_uuid, False)
                        _vm_name = _vm_attr_list["name"]
                        _obj_attr_list["parallel_operations"][_vm_counter] = {}
                        _obj_attr_list["parallel_operations"][_vm_counter]["parameters"] = _obj_attr_list["cloud_name"]  + ' ' + _vm_name + ' ' + _target_state
                        _obj_attr_list["parallel_operations"][_vm_counter]["operation"] = "vm-runstate"
                        _vm_counter += 1
    
                    if _vm_counter > int(_obj_attr_list["runstate_parallelism"]) :
                        _obj_attr_list["runstate_parallelism"] = int(_obj_attr_list["runstate_parallelism"])
                    else :
                        _obj_attr_list["runstate_parallelism"] = _vm_counter
    
                        _status, _fmsg = self.parallel_obj_operation("runstate", _obj_attr_list)

                else :
                    _fmsg = " No VMs are in state suitable for the transition "
                    _fmsg += "specified (to the \"" + _target_state + "\" state)."
                    _status = 791
            
        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except ImportError, msg :
            _status = 8
            _fmsg = str(msg)

        except AttributeError, msg :
            _status = 8
            _fmsg = str(msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "Failure while changing all suitable VMs to the state \""
                _msg += _target_state + "\" on this experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = "All suitable VMs were successfully changed to the state"
                _msg += "\"" + _target_state + "\" on this experiment"
                cbdebug(_msg)
            return _status, _msg, None
        
    @trace
    def airunstate_actual(self, obj_attr_list) :
        '''
        TBD
        '''
        _status = 0
        _target_state = obj_attr_list["target_state"].lower()
        _msg = "Going to change the state of all VMs for AI "
        _msg += obj_attr_list["name"] + " to the \"" + _target_state
        _msg += "\" state."
        cbdebug(_msg, True)

        self.runstate_list_for_ai(obj_attr_list, obj_attr_list["target_state"])
        if obj_attr_list["state_changed_vms"] != "0" :
            _status, _fmsg = self.parallel_obj_operation("runstate", obj_attr_list)
            
            if not _status :
                self.osci.set_object_state("AI", obj_attr_list["uuid"], _target_state)

        return _status, _fmsg
    
    @trace
    def airunstate(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _result = []
            _runstate_pending = False
            _fmsg = "An error has occurred, but no error message was captured"
            _obj_type = command.split('-')[0].upper()
            obj_attr_list["name"] = "undefined"
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

                if not _status :
                    self.osci.add_to_list("AI", "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], int(time()))
                    self.osci.pending_object_set("AI", obj_attr_list["uuid"], "Changing state to: " + obj_attr_list["target_state"])
                    _runstate_pending = True
                    _status, _fmsg = self.airunstate_actual(obj_attr_list)
                    _result = obj_attr_list

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            unique_state_key = "-state-" + str(time())

            if _status :
                if _status != 9 :
                    _msg = "Could not change all VMs state: " + _fmsg
                    cberr(_msg, True)
                    obj_attr_list["tracking"] = "Change state request: " + _fmsg
                else :
                    _msg = _fmsg

                if "uuid" in obj_attr_list and self.osci :
                    self.osci.create_object("FAILEDTRACKINGAI", \
                                            obj_attr_list["uuid"] + \
                                            unique_state_key, \
                                            obj_attr_list, False, True, 3600)
            else :
                _msg = "All VMs on the AI to changed to the \"" + obj_attr_list["target_state"]
                _msg += "\" state successfully."
                cbdebug(_msg, True)
                obj_attr_list["tracking"] = "Change state request: success."
                self.osci.create_object("FINISHEDTRACKINGAI", obj_attr_list["uuid"] + unique_state_key, \
                                        obj_attr_list, False, True, 3600)
                if _runstate_pending :
                    self.osci.pending_object_remove("AI", obj_attr_list["uuid"])
                    self.osci.remove_from_list("AI", "PENDING", obj_attr_list["uuid"] + "|" + obj_attr_list["name"], True)
                
            return self.package(_status, _msg, _result)

    @trace    
    def airesize(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            
            obj_attr_list["name"] = "undefined"
            obj_attr_list["uuid"] = "undefined"   
            obj_attr_list["mgt_401_resize_request_originated"] = int(time())
            _resizable_ai = True

            _obj_type = command.split('-')[0].upper()
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

            if not _status :

                obj_attr_list["parallel_operations"] = {}
                _current_state = self.osci.get_object_state("AI", obj_attr_list["uuid"])
               
                _time_mark_rrs = int(time())
                obj_attr_list["mgt_402_resize_request_sent"] = _time_mark_rrs - obj_attr_list["mgt_401_resize_request_originated"]
                           
                if _current_state and _current_state == "attached" :

                    self.osci.add_to_list("AI", "AIS_UNDERGOING_RESIZE", obj_attr_list["uuid"]) 
                    self.osci.set_object_state("AI", obj_attr_list["uuid"], "resize")
                    
                    _delta = obj_attr_list["quantity"][0]
                    _nr_vms = obj_attr_list["quantity"][1:]
                    _vm_role = obj_attr_list["role"]

                    obj_attr_list["temp_vms"] = ''
                    _vm_command_list = ''
                    _vm_counter = int(obj_attr_list["vms_nr"])
                    _vg = ValueGeneration(self.pid)
                    _nr_vms = int(_vg.get_value(_nr_vms, 0))

                    if _delta == "+" :

                        for _idx in range(0, int(_nr_vms)) :

                            obj_attr_list["parallel_operations"][_vm_counter] = {} 
                            _pobj_uuid = str(uuid5(NAMESPACE_DNS, str(randint(0,10000000000000000) + _vm_counter)))
                            _pobj_uuid = _pobj_uuid.upper()

                            obj_attr_list["temp_vms"] += _pobj_uuid + ','
                            obj_attr_list["parallel_operations"][_vm_counter]["uuid"] = _pobj_uuid
                            obj_attr_list["parallel_operations"][_vm_counter]["ai"] = obj_attr_list["uuid"]
                            obj_attr_list["parallel_operations"][_vm_counter]["aidrs"] = obj_attr_list["aidrs"]
                            obj_attr_list["parallel_operations"][_vm_counter]["type"] = obj_attr_list["type"]
                            obj_attr_list["parallel_operations"][_vm_counter]["parameters"] = obj_attr_list["cloud_name"] + ' ' + _vm_role 
                            obj_attr_list["parallel_operations"][_vm_counter]["operation"] = "vm-attach"
                            _vm_command_list += obj_attr_list["cloud_name"] + ' ' + _vm_role + ", "
                            _vm_counter += 1

                        obj_attr_list["temp_vms"] = obj_attr_list["temp_vms"][:-1]

                        _status, _fmsg = self.parallel_obj_operation("attach", obj_attr_list)

                        if not _status :
                            _vm_uuid_list = obj_attr_list["temp_vms"].split(',')

                            for _vm_uuid in _vm_uuid_list :

                                _vm_attr_list = self.osci.get_object("VM", False, _vm_uuid, False)
                                obj_attr_list["vms"] += ',' + _vm_uuid + '|' + _vm_attr_list["role"] + '|' + _vm_attr_list["name"]

                            del obj_attr_list["temp_vms"]

                            self.osci.update_object_attribute("AI", obj_attr_list["uuid"], False, "vms", obj_attr_list["vms"])
                            self.osci.update_object_attribute("AI", obj_attr_list["uuid"], False, "vms_nr", _vm_counter)

                    elif _delta == "-" :

                        _vm_list = obj_attr_list["vms"].split(',')

                        obj_attr_list["vms"] = ''
                        _destroyed_vms = 0
                        
                        _filter = True
                        for _vm in _vm_list :

                            _curr_vm_uuid, _curr_vm_role, _curr_vm_name = _vm.split('|')

                            if _curr_vm_role == _vm_role and \
                            _curr_vm_uuid != obj_attr_list["load_generator_vm"] \
                            and _curr_vm_uuid != obj_attr_list["load_manager_vm"] \
                            and _curr_vm_uuid != obj_attr_list["metric_aggregator_vm"] \
                            and _filter:

                                obj_attr_list["parallel_operations"][_vm_counter] = {}
                                obj_attr_list["parallel_operations"][_vm_counter]["uuid"] = _curr_vm_uuid
                                obj_attr_list["parallel_operations"][_vm_counter]["parameters"] = obj_attr_list["cloud_name"] + ' ' + _curr_vm_name + " true"
                                obj_attr_list["parallel_operations"][_vm_counter]["operation"] = "vm-detach"
                                _vm_command_list += obj_attr_list["cloud_name"] + ' ' + _curr_vm_name + " true" + ', '
                                _vm_counter -= 1 
                                _destroyed_vms += 1
                                if _destroyed_vms >= _nr_vms :
                                    _filter = False
                            
                            else :
                                obj_attr_list["vms"] += _vm + ','

                        obj_attr_list["vms"] = obj_attr_list["vms"][:-1]
                        obj_attr_list["vms_nr"] = _vm_counter
                        _status, _fmsg = self.parallel_obj_operation("detach", obj_attr_list)

                        _destroyed_vm_list = obj_attr_list["temp_vms"]
                        
                        if not _status :                            
                        
                            self.osci.update_object_attribute("AI", obj_attr_list["uuid"], False, "vms", obj_attr_list["vms"])
                            self.osci.update_object_attribute("AI", obj_attr_list["uuid"], False, "vms_nr", _vm_counter)
                            
                            if _destroyed_vms < _nr_vms :
                                _msg = "The request to destroy " + str(_nr_vms)
                                _msg += " VMs with the role \"" + _vm_role + "\""
                                _msg += " could not be entirely fulfilled. Only"
                                _msg += str(_destroyed_vms) + " VMs could be "
                                _msg += "destroyed."
                                cbdebug(_msg)

                    if "run_scripts" in obj_attr_list and obj_attr_list["run_scripts"].lower() != "false" :
                        _status, _fmsg  = self.parallel_vm_config_for_ai(obj_attr_list["cloud_name"], \
                                                                         obj_attr_list["uuid"], \
                                                                         "resize")
                    else :
                        _status = 0
                        _fmsg = "none"

                    if not _status :
                        self.osci.remove_from_list("AI", "AIS_UNDERGOING_RESIZE", obj_attr_list["name"])
                        self.osci.set_object_state("AI", obj_attr_list["uuid"], "attached")

                        _aux_dict = {}
                        for _vm in obj_attr_list["vms"].split(',') :
                            _vm_uuid, _vm_role, _vm_name = _vm.split('|')
                            
                            if _vm_role in _aux_dict :
                                _aux_dict[_vm_role] += 1
                            else :
                                _aux_dict[_vm_role] = 1

                            obj_attr_list["mgt_403_resize_request_completed"] = int(time()) - _time_mark_rrs
    
    
                            self.osci.update_object_attribute("VM", \
                                                              _vm_uuid, \
                                                              False, \
                                                              "mgt_401_resize_request_originated", \
                                                              obj_attr_list["mgt_401_resize_request_originated"])
        
                            self.osci.update_object_attribute("VM", \
                                                              _vm_uuid, \
                                                              False, \
                                                              "mgt_402_resize_request_sent", \
                                                              obj_attr_list["mgt_402_resize_request_sent"])
                            
                            self.osci.update_object_attribute("VM", \
                                                              _vm_uuid, \
                                                              False, \
                                                              "mgt_403_resize_request_completed", \
                                                              obj_attr_list["mgt_403_resize_request_completed"])

                        _tiers = obj_attr_list["sut"].split("->")
                        obj_attr_list["sut"] = ''

                        for _tier in _tiers :
                            _nr_vms, _vm_role = _tier.split("_x_")
                            obj_attr_list["sut"] += str(_aux_dict[_vm_role]) + "_x_" + _vm_role + "->"

                        obj_attr_list["sut"] = obj_attr_list["sut"][:-2]
                        
                        self.osci.update_object_attribute("AI", obj_attr_list["uuid"], False, "sut", obj_attr_list["sut"])

                        _status = 0

                else :

                    _fmsg = "AI object named \"" + obj_attr_list["name"] + "\" could "
                    _fmsg += "not be resized because it is on the "
                    _fmsg += "\"" + _current_state + "\" state."
                    _status = 817

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = _obj_type + " object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") could not be "
                _msg += "resized on this experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = _obj_type + " object " + obj_attr_list["uuid"] 
                _msg += " (named \"" + obj_attr_list["name"] +  "\") successfully resized "
                _msg += "on this experiment."
                cbdebug(_msg)
            return _status, _msg, None

    @trace    
    def aicapture(self, obj_attr_list, parameters, command) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _vm_name = "Unknown"
            obj_attr_list["name"] = "undefined"
            obj_attr_list["uuid"] = "undefined"

            _obj_type = command.split('-')[0].upper()
            _status, _fmsg = self.parse_cli(obj_attr_list, parameters, command)

            if not _status :
                _status, _fmsg = self.initialize_object(obj_attr_list, command)

            if not _status :
                _capture_role = obj_attr_list["capture_role"]
                for _vm in obj_attr_list["vms"].split(',') :
                    _vm_uuid, _vm_role, _vm_name = _vm.split('|')
                    if _vm_role == _capture_role :
                        _capture_vm_attr_list = {}
                        _msg = "About to call vmcapture method, from aicapture method"
                        cbdebug(_msg)
                        if "async" in obj_attr_list and obj_attr_list["async"] == "true" :
                            _status, _fmsg, _object = self.vmcapture(_capture_vm_attr_list, self.cn + ' ' + _vm_name, "vm-capture")
                        elif not BaseObjectOperations.default_cloud :
                            _cloud_name = parameters.split()[0]
                            _status, _fmsg, _object = self.vmcapture(_capture_vm_attr_list, _cloud_name + ' ' + _vm_name, "vm-capture")
                        else :
                            _status, _fmsg, _object = self.vmcapture(_capture_vm_attr_list, _vm_name, "vm-capture")
                        break

        except self.ObjectOperationException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally:        
            if _status :
                _msg = "One of the VMs (\"" + _vm_name + "\") belonging to the "
                _msg += _obj_type + " object " + obj_attr_list["uuid"] + " ("
                _msg += "named \"" + obj_attr_list["name"] + "\") could not be "
                _msg += "captured on this experiment: " + _fmsg
                cberr(_msg)
            else :
                _msg = "One of the VMs (\"" + _vm_name + "\") belonging to the "
                _msg += _obj_type + " object " + obj_attr_list["uuid"] 
                _msg += " (named \"" + obj_attr_list["name"] +  "\") successfully captured "
                _msg += "on this experiment."
                cbdebug(_msg)
            return _status, _msg, None

    @trace            
    def parallel_obj_operation(self, operation_type, obj_attr_list) :
        '''
        TBD
        '''
        try :
            _status = 100
            _fmsg = "An error has occurred, but no error message was captured"
            _rollback = False
            _obj_operation_thrpool = ThreadPool(int(obj_attr_list[operation_type + "_parallelism"]))
            for _object in obj_attr_list["parallel_operations"].keys() :
                
                obj_attr_list["parallel_operations"][_object][operation_type + "_parallel"] = "true"
                _command = obj_attr_list["parallel_operations"][_object]["parameters"]
                _operation = obj_attr_list["parallel_operations"][_object]["operation"]
                _obj_type = _operation.split('-')[0]
                
                if operation_type == "attach" :       
                    _func = self.objattach
                elif operation_type == "detach" :
                    _func = self.objdetach
                elif operation_type == "capture" and _obj_type.lower() == "vm" :
                    _func = self.vmcapture
                elif operation_type == "runstate" and _obj_type.lower() == "vm" :
                    _func = self.vmrunstate
                elif operation_type == "resize" and _obj_type.lower() == "vm" :
                    _func = self.vmresize
                elif operation_type == "resize" and _obj_type.lower() == "ai" :
                    _func = self.airesize
                else :
                    _status = 1817
                    _msg = "Unknown operation/object type"
                    cberr(_msg)
                    raise self.ObjectOperationException(_msg, _status)                    

                serial_mode = False # only used for debugging

                _tmp_list = copy.deepcopy(obj_attr_list["parallel_operations"][_object])
                
                if serial_mode : 
                    _func(_tmp_list, _command, _operation)
                else :
                    _obj_operation_thrpool.add_task(_func, _tmp_list, _command, _operation)

            _obj_operation_thrpool.wait_completion()

            # Make sure the operations succeeded
            _rollback = False
            for _object in obj_attr_list["parallel_operations"].keys() :
                _obj_type = obj_attr_list["parallel_operations"][_object]["operation"].split('-')[0].upper()
                _obj_uuid = obj_attr_list["parallel_operations"][_object]["uuid"]
                _command = obj_attr_list["parallel_operations"][_object]["parameters"]
                _exists = self.osci.object_exists(_obj_type, _obj_uuid, False) 
                if _exists and operation_type == "attach" :
                    True
                elif operation_type == "detach" :
                    if _exists :
                        _status = 19
                        break
                elif operation_type == "runstate" :
                    _state = self.osci.get_object_state("VM", _obj_uuid)
                    if _command.count("save") :
                        if _state != "save" :
                            _status = 20
                            _rollback = True
                            break
                    elif _command.count("suspend") :
                        if _state != "suspend" :
                            _status = 22
                            _rollback = True
                            break
                    elif _state != "attached" :
                        _status = 21
                        _rollback = True
                        break
                else :
                    _status = 18
                    _rollback = True
                    break
    
                if operation_type == "attach" :
                    obj_attr_list["arrival"] = int(time())

            if _rollback :
                _fmsg = "A rollback might be needed (only for VMs)."
            else :
                _status = 0

        except KeyboardInterrupt :
            _rollback = True
            _status = 42
            _fmsg = "CTRL-C interrupt"
            cbdebug("Signal children to abort...", True)
            _obj_operation_thrpool.abort()
            
        except self.ObjectOperationException, obj :
            _status = 45
            _fmsg = str(obj.msg)

        except self.oscc.ObjectStoreMgdConnException, obj :
            _status = obj.status
            _fmsg = str(obj.msg)

        except Exception, e :
            _status = 23
            _fmsg = str(e)

        finally :
            if _status :
                _msg = "Parallel object operation failure: " + _fmsg
                cberr(_msg)
                raise self.ObjectOperationException(_msg, _status)
            else :
                del obj_attr_list["parallel_operations"]
                _msg = "Parallel object operation success."
                cbdebug(_msg)
            return _status, _msg

    def aiexecute(self, cloud_name, object_type, object_uuid) :
        '''
        TBD
        '''
        _ai_state = True
        _prev_load_level = 0
        _prev_load_duration = 0
        _prev_load_id =  0

        if self.cn == cloud_name :
            True
        else :
            self.cn = cloud_name
            self.oscp["cloud_name"] = self.cn
            #self.osci.disconnect()
            self.osci = False

        self.conn_check()

        _initial_ai_attr_list = self.osci.get_object("AI", False, object_uuid, False)
        
        _mode = _initial_ai_attr_list["mode"]
        _check_frequency = float(_initial_ai_attr_list["update_frequency"])

        while _ai_state :

            if _mode == "controllable" :
                _ai_state = self.osci.get_object_state("AI", object_uuid)
                _ai_attr_list = self.osci.get_object("AI", False, object_uuid, False)
                _mode = _ai_attr_list["mode"]
                _check_frequency = float(_ai_attr_list["update_frequency"])
            else :
                _ai_state = "attached"
                _ai_attr_list = _initial_ai_attr_list
                
            if _ai_state and _ai_state == "attached" :
                _load = self.get_load(cloud_name, _ai_attr_list, False, \
                                      _prev_load_level, _prev_load_duration, \
                                      _prev_load_id)

                if _load :
                    _prev_load_level = _ai_attr_list["current_load_level"]
                    _prev_load_duration = _ai_attr_list["current_load_duration"]
                    _prev_load_id = _ai_attr_list["current_load_id"]

                if _mode == "controllable" :
                    self.update_object_attribute(cloud_name, \
                                                 object_type.upper(), \
                                                 object_uuid, \
                                                 "current_load_level", \
                                                 _ai_attr_list["current_load_level"]) 
                        
                    self.update_object_attribute(cloud_name, \
                                                 object_type.upper(), \
                                                 object_uuid, \
                                                 "current_load_duration", \
                                                 _ai_attr_list["current_load_duration"])
    
                    self.update_object_attribute(cloud_name, \
                                                 object_type.upper(), \
                                                 object_uuid, \
                                                 "current_load_id", \
                                                 _ai_attr_list["current_load_id"])
 
                _msg = "Preparing to execute AI reset"
                cbdebug(_msg)

                _reset_status, _fmsg = self.parallel_vm_config_for_ai(cloud_name, \
                                                                object_uuid, \
                                                                "reset")

                if not _reset_status :
                    _msg = "AI reset executed successfully."
                    cbdebug(_msg)
                else : 
                    _msg = "AI reset failed: " + _fmsg
                    cberr(_msg)
                    # If we fail, sleep a little and retry
                    sleep(_check_frequency * 2)

                if not _reset_status and _ai_attr_list["load_generator_ip"] == _ai_attr_list["load_manager_ip"] :
                    _cmd = "~/" + _ai_attr_list["start"] + ' '
                    _cmd += str(_ai_attr_list["current_load_level"]) + ' '
                    _cmd += str(_ai_attr_list["current_load_duration"]) + ' '
                    _cmd += str(_ai_attr_list["current_load_id"])
    
                    _load_level_time = 0
    
                    # You cannot Popen() with a PIPE unless you plan on emptying
                    # the pipe. The OS pipe has a limited buffer size and if you
                    # don't empty it, the process will block on write() to the PIPE.
                    _proc_h = Popen(_cmd, shell=True)
                    # _proc_h = Popen(_cmd, shell=True, stdout=PIPE, stderr=PIPE)
    
                    if _proc_h.pid :
                        _msg = "Load generating command \"" + _cmd + "\" "
                        _msg += " was successfully started."
                        _msg += "The process id is " + str(_proc_h.pid) + "."
                        cbdebug(_msg)
                    
                        _msg = "Waiting for the load generating process to "
                        _msg += "terminate."
                        cbdebug(_msg)
                        
                        #waitpid(-1, 0)
                        _proc_h.wait()
                else :
                    # Will have to create something here later, probably using
                    # pubsub
                    sleep(_check_frequency)

            else :
                # Only reset individual applications on the AI. Don't send
                # any load.
                _msg = object_type.upper() + " object " + object_uuid 
                _msg += " state was set to \"" + _ai_state + "\". No load will "
                _msg += "be applied until the state is changed. The "
                _msg += "current load will be allowed to finish its course."
                cbdebug(_msg)
                self.parallel_vm_config_for_ai(cloud_name, object_uuid, "reset")

        _msg = "AI \"state key\" was removed. The Load Manager will now end its execution"
        cbdebug(_msg)
        _status = 0
            
        return _status, _msg

    @trace
    def aidsubmit(self, cloud_name, base_dir, object_type, object_uuid) :
        '''
        TBD
        '''
        _aidrs_state = True

        if self.cn == cloud_name :
            True
        else :
            self.cn = cloud_name
            self.oscp["cloud_name"] = self.cn
            #self.osci.disconnect()
            self.osci = False

        self.conn_check()

        while _aidrs_state :

            _aidrs_state = self.osci.get_object_state("AIDRS", object_uuid)

            _aidrs_attr_list = self.osci.get_object("AIDRS", False, object_uuid, False)

            _check_frequency = int(_aidrs_attr_list["update_frequency"])

            _aidrs_overload, _msg = self.get_aidrs_params(cloud_name, _aidrs_attr_list)

            _inter_arrival_time = 0

            if not _aidrs_overload :
                
                if _aidrs_state and _aidrs_state != "stopped" :

                    _ai_uuid = str(uuid5(NAMESPACE_DNS, str(randint(0, \
                                                                    1000000000000000000)))).upper()

                    _cmd = base_dir + "/cbact"
                    _cmd += " --procid=" + self.pid
                    _cmd += " --osp=" + dic2str(self.oscp)

                    _cmd += " --oop=" + cloud_name + ',' + _aidrs_attr_list["type"] + ',' + _aidrs_attr_list["load_level"]
                    _cmd += ',' + _aidrs_attr_list["load_duration"] + ',' + _aidrs_attr_list["lifetime"]  + ',' + object_uuid

                    _cmd += " --operation=ai-attach"
                    _cmd += " --cn=" + cloud_name
                    _cmd += " --uuid=" + _ai_uuid
                    _cmd += " --daemon"
                    #_cmd += "  --debug_host=127.0.0.1"
                    
                    _proc_h = Popen(_cmd, shell=True, stdout=PIPE, stderr=PIPE)

                    if _proc_h.pid :
                        _msg = "AI attachment command \"" + _cmd + "\" "
                        _msg += "was successfully started."
                        _msg += "The process id is " + str(_proc_h.pid) + "."
                        cbdebug(_msg)

                        _obj_id = _ai_uuid + '-' + "attach"
                        self.update_process_list(self.cn, "AI", \
                                                 _obj_id, \
                                                 str(_proc_h.pid), \
                                                 "add")
                    else :
                        _msg = "AI attachment command \"" + _cmd + "\" "
                        _msg += "could not be successfully started."
                        cberr(_msg)
                else :
                    _msg = "Unable to get state, or state is \"stopped\"."
                    _msg += "Will stop creating new AIs until the AS state"
                    _msg += " changes."
                    cbdebug(_msg)
            else :
                _msg = "AIDRS is overloaded because " + _msg
                _msg += ". Will keep checking its state and "
                _msg += " destroying overdue AIs, but will not create new ones"
                _msg += " until the number of AIs/Daemons drops below the limit."
                cbdebug(_msg)

                if int(_aidrs_attr_list["current_inter_arrival_time"]) < _check_frequency :
                    _check_frequency = int(_aidrs_attr_list["current_inter_arrival_time"]) / 2

            _inter_arrival_time_start = time()

            while _inter_arrival_time < int(_aidrs_attr_list["current_inter_arrival_time"]) :

                _my_overdue_ais = self.osci.query_by_view("AI", "BYAIDRS", \
                                                          object_uuid, \
                                                          "departure", \
                                                          "overdue")

                if len(_my_overdue_ais) :

                    _msg = "Some AIs have reached the end of their "
                    _msg += "lifetimes, and will now be removed."
                    _msg += "Overdue AI list is :" + ','.join(_my_overdue_ais)
                    cbdebug(_msg)

                    for _ai in _my_overdue_ais :
                        _ai_uuid, _ai_name = _ai.split('|')
                        
                        _current_state = self.osci.get_object_state("AI", _ai_uuid)

                        if _current_state and _current_state == "attached" :                    
                            _cmd = base_dir + "/cbact"
                            _cmd += " --procid=" + self.pid
                            _cmd += " --osp=" + dic2str(self.oscp)
                            _cmd += " --oop=" + cloud_name + ',' + _ai_name + ',true'
                            _cmd += " --operation=ai-detach"
                            _cmd += " --cn=" + cloud_name
                            _cmd += " --uuid=" + _ai_uuid                        
                            _cmd += " --daemon"
                            #_cmd += "  --debug_host=127.0.0.1"
    
                            _proc_h = Popen(_cmd, shell=True, stdout=PIPE, stderr=PIPE)
    
                            if _proc_h.pid :
                                _msg = "Overdue AI detachment command \"" + _cmd + "\" "
                                _msg += " was successfully started."
                                _msg += "The process id is " + str(_proc_h.pid) + "."
                                cbdebug(_msg)
    
                                _obj_id = _ai_uuid + '-' + "detach"
                                self.update_process_list(self.cn, "AI", \
                                                         _obj_id, \
                                                         str(_proc_h.pid), \
                                                         "add")
                        else :
                            _msg = "AI \"" + _ai_uuid + "\" is on the \""
                            _msg += _current_state + "\" and therefore cannot "
                            _msg += "detached."
                            cbdebug(_msg)
                else :
                    _msg = "No AIs have reached the end of their lifetimes."
                    cbdebug(_msg)

                _aidrs_state = self.osci.get_object_state("AIDRS", object_uuid)

                if not _aidrs_state  :
                    _msg = "AIDRS object " + object_uuid 
                    _msg += " state could not be obtained. This process "
                    _msg += " will exit, leaving all the AIs behind."
                    cbdebug(_msg)
                    break

                elif _aidrs_state == "stopped" :
                    _msg ="AIDRS object " + object_uuid 
                    _msg += " state was set to \"stopped\"."
                    cbdebug(_msg)
                else :
                    True
                sleep(_check_frequency)

                _inter_arrival_time = time() - _inter_arrival_time_start

        _msg = "This AIDRS daemon has detected that the AIDRS object associated to it was "
        _msg += "detached. Proceeding to remove its pid from"
        _msg += " the process list before finishing."
        cbdebug(_msg)
        _status = 0

        return _status, _msg

    @trace
    def vmcrsubmit(self, cloud_name, base_dir, object_type, object_uuid) :
        '''
        TBD
        '''
        _vmcrs_state = True

        if self.cn == cloud_name :
            True
        else :
            self.cn = cloud_name
            self.oscp["cloud_name"] = self.cn
            #self.osci.disconnect()
            self.osci = False

        self.conn_check()

        while _vmcrs_state :

            _vmcrs_state = self.osci.get_object_state("VMCRS", object_uuid)

            _vmcrs_attr_list = self.osci.get_object("VMCRS", False, object_uuid, False)

            _check_frequency = float(_vmcrs_attr_list["update_frequency"])

            _ivmcat_parms = _vmcrs_attr_list["ivmcat"]
            _vg = ValueGeneration(self.pid)
            _vmcr_inter_arrival_time = int(_vg.get_value(_ivmcat_parms, 0))

            if "nr_vmcapreqs" in _vmcrs_attr_list and int(_vmcrs_attr_list["nr_vmcapreqs"]) > int(_vmcrs_attr_list["max_capreqs"]) :
                _vmcrs_overload = True
            else :
                _vmcrs_overload = False

            _inter_arrival_time = 0

            if not _vmcrs_overload :

                _msg = "The selected inter-VM Capture Request arrival time was "
                _msg += str(_vmcr_inter_arrival_time) + " seconds."
                cbdebug(_msg)

                if _vmcrs_state and _vmcrs_state != "stopped" :

                    _capturable_ais = self.osci.query_by_view("AI", "BYTYPE", _vmcrs_attr_list["type"], "arrival", "minage:" + _vmcrs_attr_list["min_cap_age"])

                    if len(_capturable_ais) :
                        _selected_ai = choice(_capturable_ais)
                        _ai_uuid, _ai_name = _selected_ai.split('|')
                        _ai_attr_list = self.osci.get_object("AI", False, _ai_uuid, False)    
                        _vm_list = _ai_attr_list["vms"].split(',')
                        _vm_candidate_list = []
                        for _vm in _vm_list :
                            _vm_uuid, _vm_role, _vm_name = _vm.split('|')
                        if _vm_role == _ai_attr_list["capture_role"] :
                            _vm_candidate_list.append(_vm)
                            _selected_vm = choice(_vm_candidate_list)

                        _vm_uuid, _vm_role, _vm_name = _vm.split('|')
                        _cmd = base_dir + "/cbact"
                        _cmd += " --procid=" + self.pid
                        _cmd += " --osp=" + dic2str(self.oscp)
                        _cmd += " --oop=" + cloud_name + ',' + _vm_name
                        _cmd += " --operation=vm-capture"
                        _cmd += " --cn=" + cloud_name
                        _cmd += " --uuid=" + _vm_uuid
                        _cmd += " --daemon"
                        #_cmd += "  --debug_host=127.0.0.1"

                        cbdebug(_cmd)
                        
                        _proc_h = Popen(_cmd, shell=True, stdout=PIPE, stderr=PIPE)
    
                        if _proc_h.pid :
                            _msg = "VM capture command \"" + _cmd + "\" "
                            _msg += " was successfully started."
                            _msg += "The process id is " + str(_proc_h.pid) + "."
                            cbdebug(_msg)

                            _obj_id = _ai_uuid + '-' + "capture"
                            self.update_process_list(self.cn, "AI", \
                                                     _obj_id, \
                                                     str(_proc_h.pid), \
                                                     "add")

                    else :
                        _msg = "No VMs are eligible for capture"
                        _inter_arrival_time = _check_frequency * 2
                        cbdebug(_msg)

                else :
                    _msg = "Unable to get state, or state is \"stopped\"."
                    _msg += "Will stop capturing new VMs until the VMCRS state"
                    _msg += " changes."
                    cbdebug(_msg)

            else :
                _msg = "VMCRS object reached maximum number"
                _msg += " of Capture Requests allowed. Will keep checking its state and "
                _msg += " destroying overdue AIs, but will not create new ones"
                _msg += " until the number of AIs drops below the limit."
                cbdebug(_msg)
                _inter_arrival_time = _check_frequency * 2

            _inter_arrival_time_start = time()

            while _inter_arrival_time < _vmcr_inter_arrival_time :

                _vmcrs_state = self.osci.get_object_state("VMCRS", object_uuid)
                
                if not _vmcrs_state  :
                    _msg = "VMCRS object " + object_uuid 
                    _msg += " state could not be obtained. This process "
                    _msg += " will exit, leaving all the AIs behind."
                    cbdebug(_msg)
                    break
                elif _vmcrs_state == "stopped" :
                    _msg ="VMCRS object " + object_uuid 
                    _msg += " state was set to \"stopped\"."
                    cbdebug(_msg)
                else :
                    True
                sleep(_check_frequency)
                _inter_arrival_time = time() - _inter_arrival_time_start

        _msg = "This VMCRS daemon has detected that the VMCRS object associated to it was "
        _msg += "detached. Proceeding to remove its pid from"
        _msg += " the process list before finishing."
        cbdebug(_msg)
        _status = 0
        
        return _status, _msg
