---
license: apache-2.0
language:
- en
- fr
- de
- es
- pt
- it
library_name: gliner
pipeline_tag: token-classification
datasets:
- urchade/synthetic-pii-ner-mistral-v1
---


# Model Card for GLiNER PII

GLiNER is a Named Entity Recognition (NER) model capable of identifying any entity type using a bidirectional transformer encoder (BERT-like). It provides a practical alternative to traditional NER models, which are limited to predefined entities, and Large Language Models (LLMs) that, despite their flexibility, are costly and large for resource-constrained scenarios.

This model has been trained by fine-tuning `urchade/gliner_multi-v2.1` on the `urchade/synthetic-pii-ner-mistral-v1` dataset.

This model is capable of recognizing various types of *personally identifiable information* (PII), including but not limited to these entity types: `person`, `organization`, `phone number`, `address`, `passport number`, `email`, `credit card number`, `social security number`, `health insurance id number`, `date of birth`, `mobile phone number`, `bank account number`, `medication`, `cpf`, `driver's license number`, `tax identification number`, `medical condition`, `identity card number`, `national id number`, `ip address`, `email address`, `iban`, `credit card expiration date`, `username`, `health insurance number`, `registration number`, `student id number`, `insurance number`, `flight number`, `landline phone number`, `blood type`, `cvv`, `reservation number`, `digital signature`, `social media handle`, `license plate number`, `cnpj`, `postal code`, `passport_number`, `serial number`, `vehicle registration number`, `credit card brand`, `fax number`, `visa number`, `insurance company`, `identity document number`, `transaction number`, `national health insurance number`, `cvc`, `birth certificate number`, `train ticket number`, `passport expiration date`, and `social_security_number`.
  
## Links

* Paper: https://arxiv.org/abs/2311.08526
* Repository: https://github.com/urchade/GLiNER

```python
from gliner import GLiNER

model = GLiNER.from_pretrained("urchade/gliner_multi_pii-v1")

text = """
Harilala Rasoanaivo, un homme d'affaires local d'Antananarivo, a enregistré une nouvelle société nommée "Rasoanaivo Enterprises" au Lot II M 92 Antohomadinika. Son numéro est le +261 32 22 345 67, et son adresse électronique est harilala.rasoanaivo@telma.mg. Il a fourni son numéro de sécu 501-02-1234 pour l'enregistrement.
"""

labels = ["work", "booking number", "personally identifiable information", "driver licence", "person", "book", "full address", "company", "actor", "character", "email", "passport number", "Social Security Number", "phone number"]
entities = model.predict_entities(text, labels)

for entity in entities:
    print(entity["text"], "=>", entity["label"])
```

```
Harilala Rasoanaivo => person
Rasoanaivo Enterprises => company
Lot II M 92 Antohomadinika => full address
+261 32 22 345 67 => phone number
harilala.rasoanaivo@telma.mg => email
501-02-1234 => Social Security Number
```