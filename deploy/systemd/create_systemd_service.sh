#!/bin/sh

#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

if [ -z "${GPROFILER_TOKEN}" ]; then echo "missing GPROFILER_TOKEN!"; exit 1; fi
if [ -z "${GPROFILER_SERVICE}" ]; then echo "missing GPROFILER_SERVICE!"; exit 1; fi

HERE=$(dirname -- "$0")
UNIT_NAME=granulate-gprofiler.service
TEMPLATE=$HERE/$UNIT_NAME.template

if [ ! -f $TEMPLATE ]; then
    echo "Downloading template"
    wget -q https://raw.githubusercontent.com/Granulate/gprofiler/master/deploy/systemd/granulate-gprofiler.service.template -O $TEMPLATE
fi


if [ -f $UNIT_NAME ]; then
    echo "${UNIT_NAME} already exists"
    while true; do
        read -p "Are you sure you want to override it? ([y]es/[n]o)" yn
        case $yn in
            [Yy]* ) echo "Removing ${UNIT_NAME}"; break;;
            [Nn]* ) exit 1;;
            * ) echo "Invalid answer";;
        esac
    done
fi

cat $TEMPLATE | sed "s/Environment=GPROFILER_TOKEN=/&${GPROFILER_TOKEN}/g;s/Environment=GPROFILER_SERVICE=/&${GPROFILER_SERVICE}/g" > $UNIT_NAME

FULL_SERVICE_FILE_PATH=$(realpath -s $UNIT_NAME)

echo "created ${FULL_SERVICE_FILE_PATH}!"
echo -e "you can now install and start the service by running:\nsystemctl enable ${FULL_SERVICE_FILE_PATH}) && systemctl start ${UNIT_NAME}"
