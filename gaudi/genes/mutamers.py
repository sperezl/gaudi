#!/usr/bin/env python
# -*- coding: utf-8 -*-

##############
# GaudiMM: Genetic Algorithms with Unrestricted
# Descriptors for Intuitive Molecular Modeling
# 
# https://github.com/insilichem/gaudi
#
# Copyright 2017 Jaime Rodriguez-Guerra, Jean-Didier Marechal
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#      http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##############

"""
This modules allows to explore side chains flexibility
in proteins, as well as mutation.

It needs that at least a :class:`gaudi.genes.rotamers.molecule.Molecule` has been
requested in the input file. Residues of those are referenced in the `residues` argument.

It also allows mutations in the selected residues. However, the resulting structure keeps
the same backbone, which may not be representative of the in-vivo behaviour. Use with caution.

"""

# Python
import random
from collections import OrderedDict
import logging
# Chimera
from AddH import simpleAddHydrogens, IdatmTypeInfo
from Rotamers import getRotamers, useRotamer as replaceRotamer, NoResidueRotamersError
import SwapRes
# External dependencies
from boltons.cacheutils import LRU
import deap.tools
# GAUDI
from gaudi import parse
from gaudi.genes import GeneProvider

logger = logging.getLogger(__name__)


def enable(**kwargs):
    kwargs = Mutamers.validate(kwargs)
    return Mutamers(**kwargs)


class Mutamers(GeneProvider):

    """
    Mutamers class

    Parameters
    ----------
    residues : list of str
        Residues that can mutate. This has to be in the form:

            [ Protein/233, Protein/109 ]

        where the first element (before slash) is the gaudi.genes.molecule name
        and the second element (after slash) is the residue position number in that
        molecule.

        This list of str is later parsed to the proper chimera.Residue objects

    library : {'Dunbrack', 'Dynameomics'}
        The rotamer library to use.

    mutations : list of str, required
        Aminoacids (in 3-letter codes) residues can mutate to.

    ligation : bool, optional
        If True, all residues will mutate to the same type of aminoacid.

    hydrogens : bool, optional
        If True, add hydrogens to replacing residues (buggy)

    Attributes
    ----------
    allele : list of 2-tuple (str, float)
        For i residues, it contains i tuples with two values each:
        residue type and a float within [0, 1), which will be used
        to pick one of the rotamers for that residue type.
    """
    _validate = {
        parse.Required('residues'): [parse.Named_spec("molecule", "residue")],
        'library': parse.Any('Dunbrack', 'dunbrack', 'Dynameomics', 'dynameomics'),
        'mutations': [parse.ResidueThreeLetterCode],
        'ligation': parse.Boolean,
        'hydrogens': parse.Boolean,
        'avoid_replacement': parse.Boolean,
        }
    
    def __init__(self, residues=None, library='Dunbrack', avoid_replacement=False,
                 mutations=[], ligation=False, hydrogens=False, **kwargs):
        GeneProvider.__init__(self, **kwargs)
        self._kwargs = kwargs
        self._residues = residues
        self.library = library
        self.mutations = mutations
        self.ligation = ligation
        self.hydrogens = hydrogens
        self.avoid_replacement = avoid_replacement
        self.allele = []
        # set caches
        try:
            self.residues = self._cache[self.name + '_residues']
        except KeyError:
            self.residues = self._cache[self.name + '_residues'] = OrderedDict()
            
        try:
            self.rotamers = self._cache[self.name + '_rotamers']
        except KeyError:
            cache_size = len(residues) * (1 + 0.5 * len(mutations))
            self.rotamers = self._cache[self.name + '_rotamers'] = LRU(int(cache_size))

        if self.ligation:
            self.random_number = random.random()
        else:
            self.random_number = None

        # Avoid unnecessary calls to expensive get_rotamers if residue is known
        # to not have any rotamers
        self._residues_without_rotamers = ['ALA', 'GLY']
    
    def __deepcopy__(self, memo):
        new = self.__class__(residues=self._residues, library=self.library, 
                             avoid_replacement=self.avoid_replacement, 
                             mutations=self.mutations[:], ligation=self.ligation, 
                             hydrogens=self.hydrogens, **self._kwargs )
        new.residues = self.residues
        new.rotamers = self.rotamers
        new.allele = self.allele[:]
        new.random_number = self.random_number
        new._residues_without_rotamers = self._residues_without_rotamers
        return new

    def __ready__(self):
        """
        Second stage of initialization.

        It parses the requested residues strings to actual residues.
        """
        for molecule, resid in self._residues:
            for res in self.parent.find_molecule(molecule).find_residues(resid):
                self.residues[(molecule, resid)] = res
                self.allele.append((self.choice(self.mutations + [res.type]),
                                    random.random()))

    def express(self):
        for (mol, pos), (restype, i) in zip(self.residues, self.allele):
            replaced = False
            try:
                residue = self.residues[(mol, pos)]
                rotamer = self.get_rotamers(mol, pos, restype)
            except NoResidueRotamersError:  # ALA, GLY...
                if residue.type != restype:
                    SwapRes.swap(residue, restype)
                    replaced = True
            else:
                rotamer_index = int(i * len(rotamer))
                if self.avoid_replacement and residue.type == restype:
                    self.update_rotamer_coords(residue, rotamer[rotamer_index])
                else:
                    replaceRotamer(residue, [rotamer[rotamer_index]])
                    replaced = True
            if replaced:
                self.residues[(mol, pos)] = \
                    next(r for r in self.parent.genes[mol].compound.mol.residues
                         if r.id.position == pos)

    def unexpress(self):
        for res in self.residues.values():
            for a in res.atoms:
                a.display = 0

    def mate(self, mate):
        if self.ligation:
            self_residues, self_rotamers = zip(*self.allele)
            mate_residues, mate_rotamers = zip(*mate.allele)
            self_rotamers, mate_rotamers = deap.tools.cxTwoPoint(
                list(self_rotamers), list(mate_rotamers))
            self.allele = map(list, zip(self_residues, self_rotamers))
            mate.allele = map(list, zip(mate_residues, mate_rotamers))
        else:
            self.allele, mate.allele = deap.tools.cxTwoPoint(
                self.allele, mate.allele)

    def mutate(self, indpb):
        if random.random() < self.indpb:
            self.allele[:] = []
            if self.ligation:  # don't forget to get a new random!
                self.random_number = random.random()
            for res in self.residues.values():
                self.allele.append(
                    (self.choice(self.mutations + [res.type]),
                        random.random()
                     )
                )

    ###

    def choice(self, l):
        """
        Overrides ``random.choice`` with custom one so we can
        reuse a previously obtained random number. This helps dealing
        with the ``ligation`` parameter, which forces all the requested
        residues to mutate to the same type
        """
        if self.random_number:
            return l[int(self.random_number * len(l))]
        return l[int(random.random() * len(l))]

    def get_rotamers(self, mol, pos, restype):
        """
        Gets the requested rotamers out of cache and if not found,
        creates the library and stores it in the cache.

        Parameters
        ----------
        mol : str
            gaudi.genes.molecule name that contains the residue
        pos : 
            Residue position in `mol`
        restype : 
            Get rotamers of selected position with this type of residue. It does
            not need to be the original type, so this allows mutations

        Returns
        -------
            List of rotamers returned by ``Rotamers.getRotamers``.
        """
        if restype in self._residues_without_rotamers:
            raise NoResidueRotamersError
        try:
            rotamers = self.rotamers[(mol, pos, restype)]
        except KeyError:
            try:
                rotamers = getRotamers(self.residues[(mol, pos)], resType=restype,
                                       lib=self.library.title())[1]
            except NoResidueRotamersError:  # ALA, GLY... has no rotamers
                self._residues_without_rotamers.append(restype)
                raise
            except KeyError:
                raise
            else:
                if self.hydrogens:
                    self.add_hydrogens_to_isolated_rotamer(rotamers)
                self.rotamers[(mol, pos, restype)] = rotamers
        return rotamers

    @staticmethod
    def update_rotamer_coords(residue, rotamer):
        rotamer = rotamer.residues[0]
        for name, rotamer_atoms in rotamer.atomsMap.items():
            for res_atom, rot_atom in zip(residue.atomsMap[name], rotamer_atoms):
                res_atom.setCoord(rot_atom.coord())

    @staticmethod
    def add_hydrogens_to_isolated_rotamer(rotamers):
        # Patch original definitions of atomtypes to account for existing bonds
        # Force trigonal planar geometry so we get a good hydrogen
        # Ideally, we'd use a tetrahedral geometry (4, 3), but with that one the
        # hydrogen we get is sometimes in direct collision with next residues' N
        patched_idatm = IdatmTypeInfo(3, 3)
        unknown_types = {}
        for rot in rotamers:
            for a in rot.atoms:
                if a.name == 'CA':
                    unknown_types[a] = patched_idatm
                    a.idatmType, a.idatmType_orig = "_CA", a.idatmType

        # Add the hydrogens
        simpleAddHydrogens(rotamers, unknownsInfo=unknown_types)
        # Undo the monkey patch
        for rot in rotamers:
            for a in rot.atoms:
                if a.name == 'CA':
                    a.idatmType = a.idatmType_orig
