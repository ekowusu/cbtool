#!/usr/bin/env bash

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

source $(echo $0 | sed -e "s/\(.*\/\)*.*/\1.\//g")/cb_common.sh

LOAD_PROFILE=$1
LOAD_LEVEL=$2
LOAD_DURATION=$3
LOAD_ID=$4

if [[ -z "$LOAD_PROFILE" || -z "$LOAD_LEVEL" || -z "$LOAD_DURATION" || -z "$LOAD_ID" ]]
then
	syslog_netcat "Usage: cb_ddgen.sh <load_profile> <load level> <load duration> <load_id>"
	exit 1
fi

BLOCK_SIZE=`get_my_ai_attribute_with_default block_size 64k`
DATA_SOURCE=`get_my_ai_attribute_with_default data_source /dev/urandom`
DATA_DESTINATION=`get_my_ai_attribute_with_default data_destination /root`

CMDLINE="sudo dd if=${DATA_SOURCE} of=${DATA_DESTINATION}/testfile.bin oflag=direct bs=${BLOCK_SIZE} count=${LOAD_LEVEL}"

syslog_netcat "Benchmarking ddgen SUT: HPCVM=${my_ip_addr} with LOAD_LEVEL=${LOAD_LEVEL} and LOAD_DURATION=${LOAD_DURATION} (LOAD_ID=${LOAD_ID} and LOAD_PROFILE=${LOAD_PROFILE})"

OUTPUT_FILE=$(mktemp)

execute_load_generator "${CMDLINE}" ${OUTPUT_FILE} ${LOAD_DURATION}
	
syslog_netcat "ddgen run complete. Will collect and report the results"

bw=`cat ${OUTPUT_FILE} | grep copied | awk '{ print $8 }'`
unbw=`cat ${OUTPUT_FILE} | grep copied | awk '{ print $9 }'`
	
~/cb_report_app_metrics.py load_id:${LOAD_ID}:seqnum \
load_level:${LOAD_LEVEL}:load \
load_profile:${LOAD_PROFILE}:name \
load_duration:${LOAD_DURATION}:sec \
bandwidth:${bw}:${unbw}

rm ${OUTPUT_FILE}

exit 0