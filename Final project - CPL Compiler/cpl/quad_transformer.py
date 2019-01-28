import abc

from lark.visitors import Transformer

from cla import CPLTokenizer
from cpl_ast import build_ast
from symbol_table import SymbolTable, Types

__author__ = "Nir Moshe"


class TemporaryVariables(object):
    variables_counter = 0

    @staticmethod
    def get_new_temporary_variable():
        TemporaryVariables.variables_counter += 1
        return "t%d" % TemporaryVariables.variables_counter

    @staticmethod
    def reset():
        TemporaryVariables.variables_counter = 0


class CPLTransformer(Transformer):
    def __init__(self, symbol_table):
        self.symbol_table = symbol_table

    def factor(self, tree):
        return Factor(tree, self.symbol_table)

    def term(self, tree):
        return Term(tree)

    def expression(self, tree):
        return Expression(tree)

    def boolfactor(self, tree):
        return BoolFactor(tree)

    def boolterm(self, tree):
        return BoolTerm(tree)

    def boolexpr(self, tree):
        return BoolExpr(tree)

    def input_stmt(self, tree):
        return InputStatement(tree, self.symbol_table)

    def output_stmt(self, tree):
        return OutputStatement(tree)

    def assignment_stmt(self, tree):
        return AssignmentStmt(tree, self.symbol_table)

    def cast_stmt(self, tree):
        return CastStmt(tree, self.symbol_table)

    def type(self, tree):
        return Types.INT if tree[0].type == "INT" else Types.FLOAT

    def declarations(self, _):
        return [] # ignore the CPL declarations.


class CPLObject(object):
    __metaclass__ = abc.ABCMeta

    def get_node_type(self):
        return self.NODE_TYPE

    @staticmethod
    def get_subtree_node_type(subtree):
        try:
            return subtree[0].get_node_type()
        except AttributeError:
            return None

    def copy_properties_of_node(self, subtree, index=0):
        self.type = subtree[index].type
        self.value = subtree[index].value
        self.code = subtree[index].code

    def handle_binary_operation(self, subtree):
        left = subtree[0]
        operator_token = subtree[1].value
        right = subtree[2]
        self.handle_binary_operation_default_values(subtree)
        self.code.append(QUADInstruction(self.value, left.value, right.value, operator_token, self.type))

    def handle_binary_operation_default_values(self, subtree):
        left = subtree[0]
        right = subtree[2]
        self.value = TemporaryVariables.get_new_temporary_variable()

        if left.type == right.type:
            self.type = left.type
            conversion_code = []
        else:
            self.type = Types.FLOAT
            temp = TemporaryVariables.get_new_temporary_variable()
            if left.type == Types.INT:
                conversion_code = [QUADInstruction(temp, left.value, "", "CAST_TO_REAL", Types.INT)]
                left.value = temp
            else:
                conversion_code = [QUADInstruction(temp, right.value, "", "CAST_TO_REAL", Types.INT)]
                right.value = temp

        # build the code.
        self.code = left.code + right.code + conversion_code

    def __str__(self):
        return "%s(%s)" % (self.__class__.__name__, self.value)


class CastStmt(CPLObject):
    NODE_TYPE = "cast_stmt"

    def __init__(self, tree, symbol_table):
        id = Factor(tree, symbol_table)
        self.type = tree[4]
        expression = tree[7]
        self.value = id.value
        if id.type == Types.INT and self.type == Types.FLOAT:
            raise ValueError("Invalid static_cast! can't assign float to int!")

        self.code = expression.code
        if expression.type == Types.INT and self.type == Types.FLOAT:
            self.code.append(QUADInstruction(self.value, expression.value, "", "CAST_TO_REAL", Types.INT))
        elif expression.type == Types.FLOAT and self.type == Types.INT:
            self.code.append(QUADInstruction(self.value, expression.value, "", "CAST_TO_INT", Types.FLOAT))


class AssignmentStmt(CPLObject):
    NODE_TYPE = "AssignmentStmt"

    def __init__(self, tree, symbol_table):
        left = Factor(tree, symbol_table)
        right = tree[2]
        if left.type == Types.INT and right.type == Types.FLOAT:
            raise ValueError("Invalid assignment! can't assign float to int!")

        self.type = Types.FLOAT if Types.FLOAT in (right.type, left.type) else Types.INT
        self.value = left.value
        self.code = right.code + [QUADInstruction(self.value, right.value, "", "=", self.type)]


class OutputStatement(CPLObject):
    NODE_TYPE = "output_stmt"

    def __init__(self, tree):
        self.copy_properties_of_node(tree, index=2)
        self.code.append(QUADInstruction(self.value, "", "", "WRITE", self.type))


class InputStatement(CPLObject):
    NODE_TYPE = "input_stmt"

    def __init__(self, tree, symbol_table):
        tree[2] = Factor([tree[2]], symbol_table)
        self.copy_properties_of_node(tree, index=2)
        self.code = [QUADInstruction(self.value, "", "", "READ", self.type)]


class BoolExpr(CPLObject):
    NODE_TYPE = "boolexpr"

    def __init__(self, tree):
        if self.get_subtree_node_type(tree) == BoolTerm.NODE_TYPE:
            self.copy_properties_of_node(tree)
        else:
            self.handle_or(tree)

    def handle_or(self, subtree):
        left = subtree[0]
        right = subtree[2]
        self.handle_binary_operation_default_values(subtree)
        self.code += QUADInstruction.get_or(self.value, left.value, right.value, self.type)


class BoolTerm(CPLObject):
    NODE_TYPE = "boolterm"

    def __init__(self, tree):
        if self.get_subtree_node_type(tree) == BoolFactor.NODE_TYPE:
            self.copy_properties_of_node(tree)
        else:
            self.handle_and(tree)

    def handle_and(self, subtree):
        left = subtree[0]
        right = subtree[2]
        self.handle_binary_operation_default_values(subtree)
        self.code += [
            QUADInstruction(self.value, left.value, 1, "==", self.type),
            QUADInstruction(self.value, right.value, self.value, "==", self.type)
        ]


class BoolFactor(CPLObject):
    NODE_TYPE = "boolfactor"

    def __init__(self, tree):
        if self.get_subtree_node_type(tree) == Expression.NODE_TYPE:
            operator = tree[1].value
            if operator == ">=":
                self.handle_larger_or_equal(tree)
            elif operator == "<=":
                self.handle_smaller_or_equal(tree)
            else:
                self.handle_binary_operation(tree)
        else:
            self.handle_boolexpression(tree)

    def handle_boolexpression(self, tree):
        self.copy_properties_of_node(tree, index=2)
        self.code.append(QUADInstruction.get_not(self.value, self.value, self.type))

    def handle_larger_or_equal(self, subtree):
        left = subtree[0]
        right = subtree[2]
        self.handle_binary_operation_default_values(subtree)
        self.value = TemporaryVariables.get_new_temporary_variable()
        temp = TemporaryVariables.get_new_temporary_variable()
        self.code += [
            QUADInstruction(temp, left.value, right.value, "==", self.type),
            QUADInstruction(self.value, left.value, right.value, ">", self.type)
        ] + QUADInstruction.get_or(self.value, self.value, temp, self.type)

    def handle_smaller_or_equal(self, subtree):
        left = subtree[0]
        right = subtree[2]
        self.handle_binary_operation_default_values(subtree)
        self.value = TemporaryVariables.get_new_temporary_variable()
        temp = TemporaryVariables.get_new_temporary_variable()
        self.code += [
            QUADInstruction(temp, left.value, right.value, "==", self.type),
            QUADInstruction(self.value, left.value, right.value, "<", self.type)
        ] + QUADInstruction.get_or(self.value, self.value, temp, self.type)


class Expression(CPLObject):
    NODE_TYPE = "expression"

    def __init__(self, subtree):
        if self.get_subtree_node_type(subtree) == Term.NODE_TYPE:
            self.copy_properties_of_node(subtree)
        else:
            self.handle_binary_operation(subtree)


class Term(CPLObject):
    NODE_TYPE = "term"

    def __init__(self, subtree):
        if self.get_subtree_node_type(subtree) == Factor.NODE_TYPE:
            self.copy_properties_of_node(subtree)
        else:
            self.handle_binary_operation(subtree)


class Factor(CPLObject):
    NODE_TYPE = "factor"

    def __init__(self, ast_subtree, symbol_table):
        first_token = ast_subtree[0]
        if first_token.type == "NUM":
            self.handle_number(ast_subtree)
        elif first_token.type == "ID":
            self.handle_id(ast_subtree, symbol_table)
        elif first_token.type == "LEFT_PARENTHESIS":
            self.copy_properties_of_node(ast_subtree, 1)

    def handle_number(self, ast_subtree):
        self.type = self.get_num_type(ast_subtree[0].value)
        self.value = (
            float(ast_subtree[0].value) if self.type == Types.FLOAT else int(ast_subtree[0].value)
        )
        self.code = []

    def handle_id(self, ast_subtree, symbol_table):
        symbol = symbol_table.get_symbol(ast_subtree[0].value)
        if not symbol:
            raise ValueError("Undefined symbol: %s" % ast_subtree[0].value)

        self.type = symbol.type
        self.value = symbol.name
        self.code = []

    @staticmethod
    def get_num_type(number):
        if type(number) == float:
            return Types.FLOAT
        else:
            return Types.INT


class QUADInstruction(object):
    QUAD_OPERATORS_TABLE = {
        ("*", Types.INT): "IMLT",
        ("*", Types.FLOAT): "RMLT",
        ("/", Types.INT): "IDIV",
        ("/", Types.FLOAT): "RDIV",
        ("+", Types.INT): "IADD",
        ("+", Types.FLOAT): "RADD",
        ("-", Types.INT): "ISUB",
        ("-", Types.FLOAT): "RSUB",
        ("==", Types.INT): "IEQL",
        ("==", Types.FLOAT): "REQL",
        ("!=", Types.INT): "INQL",
        ("!=", Types.FLOAT): "RNQL",
        (">", Types.INT): "IGRT",
        (">", Types.FLOAT): "RGRT",
        ("<", Types.INT): "ILSS",
        ("<", Types.FLOAT): "RLSS",
        ("READ", Types.INT): "IINP",
        ("READ", Types.FLOAT): "RINP",
        ("WRITE", Types.INT): "IPRT",
        ("WRITE", Types.FLOAT): "RPRT",
        ("=", Types.INT): "IASN",
        ("=", Types.FLOAT): "RASN",
        ("CAST_TO_REAL", Types.INT): "ITOR",
        ("CAST_TO_INT", Types.FLOAT): "RTOI",
    }

    def __init__(self, dest, op1, op2, operator, type):
        self.dest, self.op1, self.op2, self.type = dest, op1, op2, type
        self.operator = operator
        self.labels = set()

    @property
    def code(self):
        inst = "%s %s %s %s" % (
            self.QUAD_OPERATORS_TABLE[(self.operator, self.type)],
            self.dest,
            self.op1,
            self.op2
        )
        return inst.strip()

    @classmethod
    def get_not(cls, dest, op1, type):
        return cls(dest, op1, 1, "!=", type)

    @classmethod
    def get_or(cls, dest, op1, op2, type):
        return [cls(dest, op1, op2, "+", type), cls(dest, dest, 0, ">", type)]


if __name__ == "__main__":
    import sys
    input_filename = sys.argv[1]
    with open(input_filename) as inf:
        ast = build_ast(CPLTokenizer(inf.read()))
        print(ast)
        sym = SymbolTable.build_form_ast(ast)
        c = CPLTransformer(sym)
        #c.transform(ast)


