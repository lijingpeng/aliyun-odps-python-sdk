#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import itertools
import time
import random
from datetime import datetime, timedelta

from odps.tests.core import TestBase, to_str, tn
from odps.compat import unittest, six
from odps.models import Instance, SQLTask, Schema
from odps.errors import ODPSError
from odps import errors, compat

expected_xml_template = '''<?xml version="1.0" ?>
<Instance>
  <Job>
    <Priority>%(priority)s</Priority>
    <Tasks>
      <SQL>
        <Name>AnonymousSQLTask</Name>
        <Config>
          <Property>
            <Name>uuid</Name>
            <Value>%(uuid)s</Value>
          </Property>
          <Property>
            <Name>settings</Name>
            <Value>{"odps.sql.udf.strict.mode": "true"}</Value>
          </Property>
        </Config>
        <Query><![CDATA[%(query)s]]></Query>
      </SQL>
    </Tasks>
    <DAG>
      <RunMode>Sequence</RunMode>
    </DAG>
  </Job>
</Instance>
'''


class Test(TestBase):

    def testInstances(self):
        self.assertIs(self.odps.get_project().instances, self.odps.get_project().instances)

        size = len(list(itertools.islice(self.odps.list_instances(), 0, 200)))
        self.assertGreaterEqual(size, 0)

        instances = list(itertools.islice(
            self.odps.list_instances(status=Instance.Status.RUNNING, only_owner=True), 0, 10))
        self.assertGreaterEqual(len(instances), 0)
        if len(instances) > 0:
            # fix: use _status instead of status to prevent from fetching the instance which is just terminated
            self.assertTrue(all(instance._status == Instance.Status.RUNNING for instance in instances))
            self.assertEqual(len(set(instance.owner for instance in instances)), 1)

        from_time = time.time() - 10 * 24 * 3600
        end_time = time.time() - 24 * 3600
        instances = list(self.odps.list_instances(from_time=from_time, end_time=end_time))
        self.assertGreaterEqual(len(instances), 0)

    def testListInstancesInPage(self):
        test_table = tn('pyodps_t_tmp_list_instances_in_page')

        data = [[random.randint(0, 1000)] for _ in compat.irange(10000)]
        self.odps.delete_table(test_table, if_exists=True)
        t = self.odps.create_table(test_table, Schema.from_lists(['num'], ['bigint']))
        self.odps.write_table(t, data)

        instance = self.odps.run_sql('select sum(num) from {0} group by num'.format(test_table))

        try:
            self.assertEqual(instance.status, Instance.Status.RUNNING)
            self.assertIn(instance.id, [it.id for it in self.odps.get_project().instances.iterate(
                status=Instance.Status.RUNNING,
                from_time=datetime.now()-timedelta(days=2),
                end_time=datetime.now()+timedelta(days=1), max_items=20)])
        finally:
            try:
                instance.stop()
            except:
                pass
            t.drop()


    def testInstanceExists(self):
        non_exists_instance = 'a_non_exists_instance'
        self.assertFalse(self.odps.exist_instance(non_exists_instance))

    def testInstance(self):
        instances = self.odps.list_instances(status=Instance.Status.TERMINATED)
        instance = next(instances)

        self.assertIs(instance, self.odps.get_instance(instance.name))

        self.assertIsNotNone(instance._getattr('name'))
        self.assertIsNotNone(instance._getattr('owner'))
        self.assertIsNotNone(instance._getattr('start_time'))
        self.assertIsNotNone(instance._getattr('end_time'))
        self.assertIsNotNone(instance._getattr('_status'))
        self.assertEqual(instance._status, Instance.Status.TERMINATED)

        instance.reload()
        self.assertEqual(instance.status, Instance.Status.TERMINATED)
        self.assertTrue(instance.is_terminated())

        task_names = instance.get_task_names()
        self.assertGreater(len(task_names), 0)

        task_statuses = instance.get_task_statuses()
        for task_status in task_statuses.values():
            self.assertIn(task_status.status, (Instance.Task.TaskStatus.CANCELLED,
                                               Instance.Task.TaskStatus.FAILED,
                                               Instance.Task.TaskStatus.SUCCESS))
        for task_status in instance._tasks:
            self.assertIn(task_status.name, task_names)
            self.assertGreaterEqual(len(task_status.type), 0)
            self.assertGreaterEqual(task_status.start_time, instance.start_time)
            self.assertLessEqual(task_status.end_time, instance.end_time)

        results = instance.get_task_results()
        for name, result in results.items():
            self.assertIn(name, task_names)
            self.assertIsInstance(result, str)

        self.assertGreaterEqual(instance.priority, 0)

    def testCreateInstanceXML(self):
        instances = self.odps._project.instances

        uuid = '359696d4-ac73-4e6c-86d1-6649b01f1a22'
        query = 'select * from dual;'
        priority = 5

        task = SQLTask(query=query)
        job = instances._create_job(
            task=task, priority=priority, uuid_=uuid)
        xml = instances._get_submit_instance_content(job)
        expected_xml = expected_xml_template % {
            'query': query,
            'uuid': uuid,
            'priority': priority
        }
        self.assertEqual(to_str(xml), to_str(expected_xml))

    def testCreateInstance(self):
        test_table = tn('pyodps_t_tmp_create_instance')

        task = SQLTask(query='drop table if exists %s' % test_table)
        instance = self.odps._project.instances.create(task=task)
        instance.wait_for_completion()
        self.assertTrue(instance.is_successful())
        self.assertFalse(self.odps.exist_table(test_table))

        task = SQLTask(query='create table %s(id string);' % test_table)
        instance = self.odps._project.instances.create(task=task)
        instance.wait_for_completion()
        self.assertTrue(instance.is_successful())
        self.assertTrue(self.odps.exist_table(test_table))

        instance = self.odps.execute_sql('drop table %s' % test_table)
        self.assertTrue(instance.is_successful())
        self.assertFalse(self.odps.exist_table(test_table))

        tasks = instance.get_tasks()
        self.assertTrue(any(map(lambda task: isinstance(task, SQLTask), tasks)))

        for name in instance.get_task_names():
            self.assertIsNotNone(instance.get_task_detail(name))
            self.assertIsNotNone(instance.get_task_detail2(name))

        # test stop
        self.assertRaises(errors.InvalidStateSetting, instance.stop)

    def testReadSQLInstance(self):
        test_table = tn('pyodps_t_tmp_read_sql_instance')
        self.odps.delete_table(test_table, if_exists=True)
        table = self.odps.create_table(
            test_table, schema=Schema.from_lists(['size'], ['bigint']), if_not_exists=True)
        self.odps.write_table(
            table, 0, [table.new_record([1]), table.new_record([2])])
        self.odps.write_table(table, [table.new_record([3]), ])

        instance = self.odps.execute_sql('select * from %s' % test_table)
        with instance.open_reader(table.schema) as reader:
            self.assertEqual(len(list(reader[::2])), 2)
        with instance.open_reader(table.schema) as reader:
            self.assertEqual(len(list(reader[1::2])), 1)

        hints = {'odps.sql.mapper.split.size': 16}
        instance = self.odps.run_sql('select sum(size) as count from %s' % test_table, hints=hints)

        while len(instance.get_task_names()) == 0 or \
                compat.lvalues(instance.get_task_statuses())[0].status == Instance.Task.TaskStatus.WAITING:
            continue

        while True:
            progress = instance.get_task_progress(instance.get_task_names()[0])
            if len(progress.stages) == 0:
                continue
            self.assertGreater(len(progress.get_stage_progress_formatted_string().split()), 2)
            break

        instance.wait_for_success()

        with instance.open_reader(Schema.from_lists(['count'], ['bigint'])) as reader:
            records = list(reader)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['count'], 6)

        with instance.open_reader() as reader:
            records = list(reader)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['count'], '6')

        table.drop()

    def testReadSQLWrite(self):
        test_table = tn('pyodps_t_tmp_read_sql_instance_write')
        self.odps.delete_table(test_table, if_exists=True)
        table = self.odps.create_table(
            test_table, schema=Schema.from_lists(['size'], ['bigint']), if_not_exists=True)
        self.odps.write_table(
            table, 0, [table.new_record([1]), table.new_record([2])])
        self.odps.write_table(table, [table.new_record([3]), ])

        test_table2 = tn('pyodps_t_tmp_read_sql_instance_write2')
        self.odps.delete_table(test_table2, if_exists=True)
        table2 = self.odps.create_table(test_table2, table.schema)

        try:
            with self.odps.execute_sql('select * from %s' % test_table).open_reader() as reader:
                with table2.open_writer() as writer:
                    for record in reader:
                        writer.write(table2.new_record(record.values))
        finally:
            table.drop()
            table2.drop()

    def testReadChineseSQLInstance(self):
        test_table = tn('pyodps_t_tmp_read_chn_sql_instance')
        self.odps.delete_table(test_table, if_exists=True)
        table = self.odps.create_table(
            test_table,
            schema=Schema.from_lists(['size', 'name'], ['bigint', 'string']), if_not_exists=True)

        data = [[1, '中文'], [2, '测试数据']]
        self.odps.write_table(
            table, 0, [table.new_record(it) for it in data])

        with self.odps.execute_sql('select name from %s' % test_table).open_reader() as reader:
            read_data = sorted([to_str(r[0]) for r in reader])
            expected_data = sorted([to_str(r[1]) for r in data])

            self.assertSequenceEqual(read_data, expected_data)

        table.drop()


    def testSQLAliasInstance(self):
        test_table = tn('pyodps_t_tmp_sql_aliases_instance')
        self.odps.delete_table(test_table, if_exists=True)
        table = self.odps.create_table(
            test_table,
            schema=Schema.from_lists(['size'], ['bigint']),
            if_not_exists=True
        )

        data = [[1, ], ]
        self.odps.write_table(table, 0, data)

        res_name1 = tn('pyodps_t_tmp_resource_1')
        res_name2 = tn('pyodps_t_tmp_resource_2')
        try:
            self.odps.delete_resource(res_name1)
        except ODPSError:
            pass
        try:
            self.odps.delete_resource(res_name2)
        except ODPSError:
            pass
        res1 = self.odps.create_resource(res_name1, 'file', file_obj='1')
        res2 = self.odps.create_resource(res_name2, 'file', file_obj='2')

        test_func_content = """\
from odps.udf import annotate
from odps.distcache import get_cache_file

@annotate('bigint->bigint')
class Example(object):
    def __init__(self):
        self.n = int(get_cache_file('%s').read())

    def evaluate(self, arg):
        return arg + self.n
""" % res_name1
        py_res_name = tn('pyodps_t_tmp_func_res')
        try:
            self.odps.delete_resource(py_res_name+'.py')
        except ODPSError:
            pass

        py_res = self.odps.create_resource(py_res_name+'.py', 'py', file_obj=test_func_content)

        test_func_name = tn('pyodps_t_tmp_func_1')
        try:
            self.odps.delete_function(test_func_name)
        except ODPSError:
            pass
        func = self.odps.create_function(test_func_name,
                                         class_type='{0}.Example'.format(py_res_name),
                                         resources=[py_res_name+'.py', res_name1])

        for i in range(1, 3):
            aliases = None
            if i == 2:
                aliases = {
                    res_name1: res_name2
                }
            with self.odps.execute_sql(
                    'select %s(size) from %s' % (test_func_name, test_table),
                    aliases=aliases).open_reader() as reader:
                data = reader[0]
                self.assertEqual(int(data[0]), i + 1)

        for obj in (func, py_res, res1, res2, table):
            obj.drop()

    def testInstanceLogview(self):
        instance = self.odps.run_sql('drop table if exists non_exist_table_name')
        self.assertIsInstance(self.odps.get_logview_address(instance.id, 12), six.string_types)


if __name__ == '__main__':
    unittest.main()
