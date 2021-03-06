from django.db.models import Model, CharField, IntegerField, Func, F

from triggers.pl_python.builder import plfunction, pltrigger


@plfunction
def pl_max(a: int,
          b: int) -> int:
    if a > b:
        return a
    return b


@pltrigger(event="INSERT",
           when="BEFORE",
           table="triggers_book")
def pl_trigger(td, plpy):
    td['new']['name'] = td['new']['name'] + 'test'


class Book(Model):
    name = CharField(max_length=10)
    amount_stock = IntegerField(default=20)
    amount_sold = IntegerField(default=10)

    def get_max(self):
        return Book.objects.annotate(
            max_value=Func(F('amount_sold'), F('amount_stock'), function='pl_max')
        ).get(pk=self.pk).max_value
