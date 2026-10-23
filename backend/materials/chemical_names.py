"""Small compositional name parser, no guessed variable metal oxidation states."""
import math, re
from pymatgen.core import Element, Composition

def binary_name(query):
    m=re.fullmatch(r'([a-z]+)(?:\s*\(([ivx]+)\))?\s+(oxide|fluoride|chloride|bromide|iodide|nitride|sulfide)',query.strip(),re.I)
    if not m:return None
    name,roman,anion=m.groups(); names={e.long_name.casefold():e for e in Element}
    names['aluminum']=Element('Al');names['cesium']=Element('Cs')
    metal=names.get(name.casefold())
    if metal is None:return None
    charge={'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6}.get((roman or '').upper())
    if not roman:
        charge=1 if metal.group==1 and metal.symbol!='H' else 2 if metal.group==2 else {'Al':3,'Zn':2}.get(metal.symbol)
    if charge is None:return None
    atom,negative={'oxide':('O',2),'fluoride':('F',1),'chloride':('Cl',1),'bromide':('Br',1),'iodide':('I',1),'nitride':('N',3),'sulfide':('S',2)}[anion.lower()]
    g=math.gcd(charge,negative)
    return Composition({metal.symbol:negative//g,atom:charge//g}).reduced_formula
