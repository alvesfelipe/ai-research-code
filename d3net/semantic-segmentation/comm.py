# Copyright 2021 Sony Group Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

'''
NNabla Utility Code for Communicators
'''

import nnabla as nn
from nnabla.logger import logger
from nnabla.ext_utils import get_extension_context
import nnabla.communicators as C
import logging


def init_nnabla(conf=None, ext_name=None, device_id=None, type_config=None):
    if conf is None:
        conf = AttrDict()
    if ext_name is not None:
        conf.ext_name = ext_name
    if device_id is not None:
        conf.device_id = device_id
    if type_config is not None:
        conf.type_config = type_config

    # set context
    ctx = get_extension_context(
        ext_name=conf.ext_name, device_id=conf.device_id, type_config=conf.type_config)

    # init communicator
    comm = CommunicatorWrapper(ctx)
    nn.set_default_context(comm.ctx)

    # disable outputs from logger except rank==0
    if comm.rank > 0:
        logger.setLevel(logging.ERROR)

    return comm


class AttrDict(dict):
    # special internal variable used for error message.
    _parent = []

    def __setattr__(self, key, value):
        if key == "_parent":
            self.__dict__["_parent"] = value
            return

        self[key] = value

    def __getattr__(self, key):
        if key not in self:
            raise AttributeError(
                "dict (AttrDict) has no chain of attributes '{}'".format(".".join(self._parent + [key])))

        if isinstance(self[key], dict):
            self[key] = AttrDict(self[key])
            self[key]._parent = self._parent + [key]

        return self[key]

    def dump_to_stdout(self):
        print("================================configs================================")
        for k, v in self.items():
            print("{}: {}".format(k, v))

        print("=======================================================================")


def create_float_context(ctx):
    ctx_float = get_extension_context(ctx.backend[0].split(':')[0], device_id=ctx.device_id)
    return ctx_float


class CommunicatorWrapper(object):
    def __init__(self, ctx):
        try:
            comm = C.MultiProcessDataParallelCommunicator(ctx)
            comm.init()
            self.n_procs = comm.size
            self.rank = comm.rank
            self.local_rank = comm.local_rank
            self.comm = comm
        except Exception as e:
            print(e)
            print('No communicator found. Running with a single process. If you run this with MPI processes,'
                  ' all processes will perform totally same.')
            self.n_procs = 1
            self.rank = 0
            self.local_rank = 0
            self.comm = None

        ctx.device_id = str(int(ctx.device_id) + int(self.local_rank))
        self.ctx = ctx
        self.ctx_float = create_float_context(ctx)

        logger.info("[Communicator] Using gpu_id = {} as rank = {}".format(
            self.ctx.device_id, self.rank))

    def all_reduce(self, params, division, inplace):
        if self.n_procs == 1:
            # skip all reduce since no processes have to be all-reduced
            return
        self.comm.all_reduce(params, division=division, inplace=inplace)

    def barrier(self):
        if self.n_procs == 1:
            return
        self.comm.barrier()

    def all_reduced_solver_update(self, solver, division=False, inplace=True):
        if self.n_procs > 1:
            params = [
                x.grad for x in solver.get_parameters().values()]
            self.all_reduce(params, division=division, inplace=inplace)

        solver.update()

    def all_reduced_solver_update_all(self, *solvers, division=False, inplace=True):
        for solver in solvers:
            self.all_reduced_solver_update(
                solver, division=division, inplace=inplace)

    def get_all_reduce_callback(self, packing_size=2 << 20):
        callback = None
        if self.n_procs > 1:
            params = [x.grad for x in nn.get_parameters().values()]
            callback = self.comm.all_reduce_callback(
                params, packing_size)
        return callback
