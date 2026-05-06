from tests.nf_validation import validate_nextflow

code_file = open("../test.nf", "r")
content = code_file.read()
print(content)
print(validate_nextflow(content))
