import json

import requests
from channels.generic.websocket import WebsocketConsumer
from rest_framework import status
from rest_framework.response import Response

from Alltechmanagement.models import SHOP2_STOCK_FIX
from Alltechmanagement.serializers import shop2_serializer
from Alltechmanagement.views import get_token

'''
class Consumer(WebsocketConsumer):
    def connect(self):
        self.accept()

        self.send(text_data=json.dumps({"value":"hello there"}))

        #self.close()

    def disconnect(self, close_code):
        pass

    def receive(self, text_data):
        #self.send(text_data=text_data)
        #self.close()
        data = json.loads(text_data)
        operation = data.get('operation')
        if operation:

            if operation == 'create':
                self.create(data)
            elif operation == 'test':
                self.send('received')
            elif operation == 'getStock':
                self.get_shop_stock()
        else:
            self.close()

    def create(self, text_data):
        print(text_data)
        serializer = shop2_serializer(data=text_data['data'])
        if serializer != serializer.is_valid():
            print(serializer.errors)

        if serializer.is_valid(raise_exception=True):
            serializer.save()
            self.send_response('create', serializer.data, status.HTTP_201_CREATED)
            token = get_token()
            product_name = serializer.data['product_name']
            price = serializer.data['price']
            quantity = serializer.data['quantity']
            headers = {'Authorization': 'Bearer ' + token}
            body = {
                'id': serializer.data['id'],
                'product_name': product_name,
                'price': price,
                'quantity': quantity,
            }

            try:
                response = requests.post('https://meilisearch-query.onrender.com/shop2stock', headers=headers,
                                         json=body)
                response_data = response.json()
                print(response_data)
                if response.status_code == 200:


                    return Response(serializer.data, status=status.HTTP_200_OK)
                else:
                    return Response(response_data, status=response.status_code)

            except requests.exceptions.RequestException as e:
                self.send_response('create', serializer.data, status.HTTP_500_INTERNAL_SERVER_ERROR)
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    def get_shop_stock(self):
        data = SHOP2_STOCK_FIX.objects.all()
        serializer = shop2_serializer(instance=data, many=True)
        self.send_response('getStock', serializer.data, status=status.HTTP_200_OK)
        return Response({'data': serializer.data})

    def delete_stock2_api(self, text_data):
        try:
            query = text_data['data']
            data = SHOP2_STOCK_FIX.objects.get(pk=query.id)
            token = get_token()
            headers = {'Authorization': 'Bearer ' + token}
            response = requests.delete(f'https://meilisearch-query.onrender.com/delete2/{data.id}', headers=headers,
                                       )
            response_data = response.json()
            print(response_data)
            if response.status_code == 200:
                data.delete()
                return Response(response_data, status=status.HTTP_200_OK)
            else:
                return Response(response_data, status=response.status_code)
        except Exception as e:
            return Response({"Error": e})

    def send_response(self, action, data,status):
        self.send(text_data=json.dumps({'action': action, 'data': data,'status':status}))'''

